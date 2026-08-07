"""动态限流中间件：按租户 API Key 限流，支持运行时配置。

Slowapi 的 @limiter.limit 是静态装饰器，无法按租户动态切换限流值。
本模块提供基于滑动窗口的限流，每个租户按 `rate_limit_per_user` 独立计算。

存储后端（多 Worker 共享限流状态）：
  - Redis（生产环境，多进程共享，需配置 RATE_LIMIT_STORAGE_URI）
  - 内存（单进程，Redis 不可用时自动降级）

Examples:
    >>> from src.utils.dynamic_limiter import project_limiter
    >>> project_limiter.is_allowed(request, project)
    True
    >>> project_limiter.get_remaining(request, project)
    42
"""

import time
import threading
from typing import Optional

from fastapi import Request

from src.domain.project import Project
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ================================================================
# Lua 脚本：原子滑动窗口（Redis 多 Worker 共享）
# ================================================================

# 检查 + 记录（原子）：返回 [allowed, current_count]
# ARGV: [现时间戳, 窗口秒数, 上限, 唯一 member]
_ALLOW_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, ARGV[1] - ARGV[2])
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[3]) then
  return {0, count}
end
redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[2])
return {1, count + 1}
"""

# 查询剩余额度（只读）：返回 remaining
_REMAINING_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, ARGV[1] - ARGV[2])
local count = redis.call('ZCARD', KEYS[1])
return tonumber(ARGV[3]) - count
"""

# 限流 Key 前缀（隔离 Redis 命名空间）
_REDIS_PREFIX = "rl"


# ================================================================
# 内存滑动窗口（单进程降级）
# ================================================================


class _SlidingWindow:
    """单个 key 的滑动窗口限流器（内存实现，单进程）。"""

    def __init__(self, max_requests: int, window_seconds: int):
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def is_allowed(self) -> bool:
        """滑动窗口检查 + 写入时间戳，返回是否允许。"""
        now = time.monotonic()
        with self._lock:
            cutoff = now - self._window_seconds
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            if len(self._timestamps) >= self._max_requests:
                return False
            self._timestamps.append(now)
            return True

    def remaining(self) -> int:
        """返回当前窗口内剩余可请求次数。"""
        now = time.monotonic()
        with self._lock:
            cutoff = now - self._window_seconds
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            return max(0, self._max_requests - len(self._timestamps))


# ================================================================
# 工具函数
# ================================================================


def _parse_rate(rate_str: str) -> tuple[int, int]:
    """解析限流字符串，如 '60/minute' → (60, 60)。"""
    if "/" not in rate_str:
        return 60, 60
    parts = rate_str.lower().split("/")
    try:
        count = int(parts[0])
    except ValueError:
        return 60, 60
    unit_map = {
        "second": 1,
        "minute": 60,
        "hour": 3600,
        "day": 86400,
    }
    unit = parts[1].strip() if len(parts) > 1 else "minute"
    return count, unit_map.get(unit, 60)


def _redis_key(key: str) -> str:
    """构造 Redis 限流 Key。"""
    return f"{_REDIS_PREFIX}:{key}"


# ================================================================
# 主限流器
# ================================================================


class ProjectLimiter:
    """按租户动态限流器。

    每个租户按 (project_id, api_key) 独立计算滑动窗口，
    未携带租户的公开端点按 IP 限流。

    支持 Redis（多 Worker 共享）和内存（降级）两种后端。
    """

    def __init__(self):
        self._memory_windows: dict[str, _SlidingWindow] = {}
        self._lock = threading.RLock()
        self._redis = None
        self._lua_allow = None
        self._lua_remaining = None
        self._init_redis()

    def _init_redis(self) -> None:
        """初始化 Redis 客户端（如已配置且可连接）。"""
        uri = settings.rate_limit.storage_uri
        if not uri or uri == "memory://":
            logger.info("ProjectLimiter 使用内存存储")
            return

        try:
            from redis import Redis

            self._redis = Redis.from_url(
                uri,
                socket_connect_timeout=2,
                socket_timeout=2,
                decode_responses=False,
            )
            self._redis.ping()
            self._lua_allow = self._redis.register_script(_ALLOW_SCRIPT)
            self._lua_remaining = self._redis.register_script(_REMAINING_SCRIPT)
            logger.info(f"ProjectLimiter 使用 Redis 存储: {uri}")
        except Exception as e:
            logger.warning(f"Redis 连接失败，降级到内存限流: {e}")
            self._redis = None
            self._lua_allow = None
            self._lua_remaining = None

    def _get_key(self, request: Request, project: Optional[Project] = None) -> str:
        """构造限流 key。"""
        if project:
            return f"project:{project.project_id}"
        # 无租户按 IP 限流
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"
        return f"ip:{request.client.host if request.client else 'unknown'}"

    def _get_or_create_memory_window(
        self, key: str, max_requests: int, window_seconds: int
    ) -> _SlidingWindow:
        """获取或创建内存滑动窗口，支持运行时更新限流配置。"""
        if key not in self._memory_windows:
            self._memory_windows[key] = _SlidingWindow(max_requests, window_seconds)
        else:
            w = self._memory_windows[key]
            if w._max_requests != max_requests or w._window_seconds != window_seconds:
                self._memory_windows[key] = _SlidingWindow(max_requests, window_seconds)
        return self._memory_windows[key]

    def is_allowed(self, request: Request, project: Optional[Project] = None) -> bool:
        """检查请求是否允许。

        优先使用 Redis（多 Worker 共享），失败时降级到内存。
        """
        key = self._get_key(request, project)
        max_req, window_sec = _parse_rate(
            project.rate_limit_per_user if project else "60/minute"
        )

        # Redis 路径
        if self._redis is not None:
            try:
                import uuid
                now = time.time()
                allowed, _ = self._lua_allow(
                    keys=[_redis_key(key)],
                    args=[now, window_sec, max_req, uuid.uuid4().hex],
                )
                return bool(allowed)
            except Exception as e:
                logger.warning(f"Redis 限流失败，降级到内存: {e}")

        # 内存降级路径
        return self._get_or_create_memory_window(key, max_req, window_sec).is_allowed()

    def get_remaining(self, request: Request, project: Optional[Project] = None) -> int:
        """返回当前窗口内剩余可请求次数。"""
        key = self._get_key(request, project)
        max_req, window_sec = _parse_rate(
            project.rate_limit_per_user if project else "60/minute"
        )

        # Redis 路径
        if self._redis is not None:
            try:
                now = time.time()
                remaining = self._lua_remaining(
                    keys=[_redis_key(key)],
                    args=[now, window_sec, max_req],
                )
                return max(0, remaining)
            except Exception as e:
                logger.warning(f"Redis 限流查询失败，降级到内存: {e}")

        # 内存降级路径
        if key in self._memory_windows:
            return self._memory_windows[key].remaining()
        return max_req  # 未限流


# 全局实例
project_limiter = ProjectLimiter()