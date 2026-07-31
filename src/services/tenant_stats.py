"""租户统计注册表：记录每个租户的 LLM 调用、Token 使用和缓存命中率。

线程安全：使用 threading.Lock 保护所有读写操作。

数据在内存中，重启后丢失。如需持久化可扩展到 Redis/DB。

使用方式：
    registry = TenantStatsRegistry()
    # 在 Retriever 中：
    registry.record(tenant_id="t1", cache_hit=True, prompt_tokens=100, completion_tokens=50)
    # 在 stats 端点中：
    stats = registry.get_stats("t1")
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


@dataclass
class TenantStats:
    """单个租户的统计信息。"""

    total_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hits: int = 0
    last_call_at: float = 0.0  # unix timestamp

    @property
    def cache_hit_rate(self) -> float:
        """缓存命中率。"""
        if self.total_calls == 0:
            return 0.0
        return round(self.cache_hits / self.total_calls, 4)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TenantStatsRegistry:
    """租户统计注册表。"""

    def __init__(self):
        self._stats: Dict[str, TenantStats] = {}
        self._lock = threading.Lock()

    def _get_or_create(self, tenant_id: str) -> TenantStats:
        """获取或创建租户统计（需在调用方持锁）。"""
        if tenant_id not in self._stats:
            self._stats[tenant_id] = TenantStats()
        return self._stats[tenant_id]

    def record(
        self,
        tenant_id: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_hit: bool = False,
    ) -> None:
        """记录一次 LLM 调用。"""
        now = datetime.now().timestamp()
        with self._lock:
            s = self._get_or_create(tenant_id)
            s.total_calls += 1
            s.prompt_tokens += prompt_tokens
            s.completion_tokens += completion_tokens
            if cache_hit:
                s.cache_hits += 1
            s.last_call_at = now

    def get_stats(self, tenant_id: str) -> Optional[TenantStats]:
        """获取租户统计。"""
        with self._lock:
            return self._stats.get(tenant_id)

    def reset(self, tenant_id: Optional[str] = None) -> None:
        """重置统计：不指定 tenant_id 则清空所有。"""
        with self._lock:
            if tenant_id:
                self._stats.pop(tenant_id, None)
            else:
                self._stats.clear()
