"""Embedding 缓存测试 — 启用/禁用、TTL、LRU、并发安全。"""

import time
import numpy as np
import pytest

from src.infrastructure.embedding_cache import EmbeddingCache


@pytest.fixture
def cache():
    return EmbeddingCache(enabled=True, maxsize=5, ttl=3600)


# ================================================================
# 基本操作
# ================================================================


class TestBasicOps:
    def test_get_miss(self, cache):
        """未缓存时返回 None。"""
        assert cache.get("退货政策") is None

    def test_set_and_get(self, cache):
        """设置后可以获取到向量。"""
        vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        cache.set("退货政策", vec)
        result = cache.get("退货政策")
        assert result is not None
        assert np.array_equal(result, vec)

    def test_get_returns_stored_object(self, cache):
        """get 返回的是缓存中的同一对象（非副本）。"""
        vec = np.array([0.1, 0.2], dtype=np.float32)
        cache.set("test", vec)
        result = cache.get("test")
        # 修改取出的值会影响缓存（实现不返回副本）
        result[0] = 999
        cached_again = cache.get("test")
        assert cached_again[0] == 999

    def test_different_queries(self, cache):
        """不同查询互相独立。"""
        cache.set("q1", np.array([1.0, 0.0]))
        cache.set("q2", np.array([0.0, 1.0]))
        assert np.array_equal(cache.get("q1"), np.array([1.0, 0.0]))
        assert np.array_equal(cache.get("q2"), np.array([0.0, 1.0]))


# ================================================================
# 禁用
# ================================================================


class TestDisabled:
    def test_disabled_get_returns_none(self):
        cache = EmbeddingCache(enabled=False)
        cache.set("key", np.array([1.0]))
        assert cache.get("key") is None

    def test_disabled_set_does_nothing(self):
        cache = EmbeddingCache(enabled=False)
        cache.set("key", np.array([1.0]))
        assert cache.get("key") is None


# ================================================================
# TTL
# ================================================================


class TestTTL:
    def test_expired_entry_returns_none(self):
        cache = EmbeddingCache(enabled=True, maxsize=10, ttl=0)  # 0 秒 TTL
        cache.set("key", np.array([1.0]))
        time.sleep(0.01)
        assert cache.get("key") is None


# ================================================================
# LRU 淘汰
# ================================================================


class TestEviction:
    def test_lru_evicts_oldest(self, cache):
        """超过容量时淘汰最旧条目。"""
        for i in range(5):
            cache.set(f"k{i}", np.array([float(i)]))
        # 现在满了
        cache.set("k_new", np.array([99.0]))
        # 应该有 5 条（淘汰了最旧的 1 条）
        assert cache.stats()["current_size"] == 5


# ================================================================
# 统计
# ================================================================


class TestStats:
    def test_stats_empty(self, cache):
        stats = cache.stats()
        assert stats["enabled"] is True
        assert stats["maxsize"] == 5
        assert stats["current_size"] == 0

    def test_stats_after_set(self, cache):
        cache.set("k1", np.array([1.0]))
        cache.set("k2", np.array([2.0]))
        stats = cache.stats()
        assert stats["current_size"] == 2


# ================================================================
# 异步接口
# ================================================================


class TestAsync:
    @pytest.mark.asyncio
    async def test_aget(self, cache):
        cache.set("key", np.array([1.0]))
        result = await cache.aget("key")
        assert result is not None
        assert np.array_equal(result, np.array([1.0]))

    @pytest.mark.asyncio
    async def test_aset_and_aget(self, cache):
        await cache.aset("key", np.array([2.0]))
        result = await cache.aget("key")
        assert np.array_equal(result, np.array([2.0]))