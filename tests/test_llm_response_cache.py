"""LLM 响应缓存测试。"""

import numpy as np
import pytest

from src.infrastructure.llm_response_cache import LLMResponseCache


@pytest.fixture
def cache():
    return LLMResponseCache(maxsize=100, ttl=3600, threshold=0.95)


class TestLLMResponseCache:
    """LLM 响应缓存测试。"""

    def test_set_and_get_exact_match(self, cache):
        """相同向量应命中缓存。"""
        vec = np.random.rand(384).astype(np.float32)
        cache.set(vec, "cached response")
        result = cache.get(vec)
        assert result == "cached response"

    def test_get_miss_for_different_query(self, cache):
        """不同向量不应命中缓存。"""
        vec1 = np.random.rand(384).astype(np.float32)
        vec2 = np.random.rand(384).astype(np.float32)
        cache.set(vec1, "response1")
        result = cache.get(vec2)
        assert result is None

    def test_get_returns_none_when_empty(self, cache):
        """空缓存应返回 None。"""
        vec = np.random.rand(384).astype(np.float32)
        assert cache.get(vec) is None

    def test_similar_query_hits_cache(self, cache):
        """高度相似的向量应命中缓存。"""
        vec1 = np.random.rand(384).astype(np.float32)
        vec1 = vec1 / np.linalg.norm(vec1)
        # 生成一个与 vec1 高度相似的向量
        vec2 = vec1 * 0.99 + np.random.rand(384).astype(np.float32) * 0.01
        vec2 = vec2 / np.linalg.norm(vec2)

        cache.set(vec1, "similar response")
        result = cache.get(vec2)
        assert result == "similar response"

    def test_ttl_expiration(self, cache):
        """TTL 过期后缓存应失效。"""
        short_cache = LLMResponseCache(maxsize=10, ttl=1, threshold=0.95)
        vec = np.random.rand(384).astype(np.float32)
        short_cache.set(vec, "will expire")
        import time
        time.sleep(1.5)
        assert short_cache.get(vec) is None