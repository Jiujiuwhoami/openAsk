"""查询 Embedding 缓存。

相同查询文本 → 复用之前计算好的向量，跳过 Sentence-BERT 编码。
基于 LRU（functools.lru_cache）+ TTL 过期，纯内存，线程安全。

为什么可以安全缓存？
- Sentence-BERT 对同一文本总是输出相同向量（确定性模型，无 dropout）
- 查询文本是用户输入，与运行时无关
- 384 维 float32 = 1.5KB/条，10000 条 ≈ 15MB 内存
"""

import asyncio
import hashlib
import threading
import time
from typing import Optional

import numpy as np

from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


class EmbeddingCache:
    """查询 Embedding 缓存（线程安全，内存 LRU + TTL）。

    用法:
        cache = EmbeddingCache()
        vec = cache.get("退货政策是什么？")  # 可能返回 None（未缓存）
        cache.set("退货政策是什么？", embedding_vector)
    """

    def __init__(
        self,
        enabled: bool = None,
        maxsize: int = None,
        ttl: int = None,
    ):
        cfg = settings.embedding_cache
        self._enabled = enabled if enabled is not None else cfg.enabled
        self._maxsize = maxsize if maxsize is not None else cfg.maxsize
        self._ttl = ttl if ttl is not None else cfg.ttl
        self._lock = threading.RLock()
        # dict[query_hash] = (embedding_vector, timestamp)
        self._store: dict[str, tuple] = {}

    def _hash_key(self, query: str) -> str:
        """生成查询哈希作为缓存 key，避免把整个 query 存到 dict key。"""
        return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]

    def get(self, query: str) -> Optional[np.ndarray]:
        """从缓存获取查询向量。"""
        if not self._enabled:
            return None

        key = self._hash_key(query)
        with self._lock:
            if key not in self._store:
                return None
            vec, ts = self._store[key]
            if time.monotonic() - ts > self._ttl:
                del self._store[key]
                return None
            logger.debug(f"Embedding 缓存命中: {query[:40]}...")
            return vec

    def set(self, query: str, vec: np.ndarray) -> None:
        """写入缓存。"""
        if not self._enabled:
            return

        key = self._hash_key(query)
        with self._lock:
            # LRU：超过容量时随机淘汰（简单策略，避免维护访问顺序）
            if key not in self._store and len(self._store) >= self._maxsize:
                oldest_key = min(self._store.keys())
                del self._store[oldest_key]
            self._store[key] = (np.array(vec, dtype=np.float32), time.monotonic())

    async def aget(self, query: str) -> Optional[np.ndarray]:
        """异步获取（非阻塞，内存操作直接返回）。"""
        return self.get(query)

    async def aset(self, query: str, vec: np.ndarray) -> None:
        """异步写入（非阻塞）。"""
        self.set(query, vec)

    def stats(self) -> dict:
        """返回缓存统计。"""
        with self._lock:
            return {
                "enabled": self._enabled,
                "maxsize": self._maxsize,
                "ttl": self._ttl,
                "current_size": len(self._store),
            }
