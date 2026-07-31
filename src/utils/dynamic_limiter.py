"""动态限流中间件：按租户 API Key 限流，支持运行时配置。

Slowapi 的 @limiter.limit 是静态装饰器，无法按租户动态切换限流值。
本模块提供基于滑动窗口的限流，每个租户按 `rate_limit_per_user` 独立计算。
"""

import time
import threading
from collections import defaultdict
from typing import Optional

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse

from src.domain.models import Tenant
from src.utils.logger import get_logger

logger = get_logger(__name__)


class _SlidingWindow:
    """单个 key 的滑动窗口限流器。"""

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


class TenantLimiter:
    """按租户动态限流器。

    每个租户按 (tenant_id, api_key) 独立计算滑动窗口，
    未携带租户的公开端点按 IP 限流。
    """

    def __init__(self):
        self._windows: dict[str, _SlidingWindow] = {}
        self._lock = threading.RLock()

    def _get_or_create_window(
        self, key: str, tenant: Optional[Tenant]
    ) -> _SlidingWindow:
        """获取或创建滑动窗口，支持运行时更新限流配置。"""
        if key not in self._windows:
            max_req, window_sec = _parse_rate(tenant.rate_limit_per_user if tenant else "60/minute")
            self._windows[key] = _SlidingWindow(max_req, window_sec)
        else:
            # 运行时更新配置（如果租户配置变了）
            if tenant:
                max_req, window_sec = _parse_rate(tenant.rate_limit_per_user)
                if (
                    self._windows[key]._max_requests != max_req
                    or self._windows[key]._window_seconds != window_sec
                ):
                    self._windows[key] = _SlidingWindow(max_req, window_sec)
        return self._windows[key]

    def _get_key(self, request: Request, tenant: Optional[Tenant] = None) -> str:
        """构造限流 key。"""
        if tenant:
            return f"tenant:{tenant.tenant_id}"
        # 无租户按 IP 限流
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"
        return f"ip:{request.client.host if request.client else 'unknown'}"

    def is_allowed(self, request: Request, tenant: Optional[Tenant] = None) -> bool:
        """检查请求是否允许。"""
        key = self._get_key(request, tenant)
        window = self._get_or_create_window(key, tenant)
        return window.is_allowed()

    def get_remaining(self, request: Request, tenant: Optional[Tenant] = None) -> int:
        """返回剩余可请求次数。"""
        key = self._get_key(request, tenant)
        if key in self._windows:
            return self._windows[key].remaining()
        return 999  # 未限流


# 全局实例
tenant_limiter = TenantLimiter()
