"""Retriever 扩展测试 — stream, _build_context, _fallback_answer, _check_handoff, close, __aenter__. """

from unittest.mock import Mock, AsyncMock, MagicMock

import numpy as np
import pytest

from src.core.retriever import Retriever, RetrievalResult
from src.domain.models import SearchResult
from src.domain.exceptions import EmbeddingError, VectorStoreError, SenseNovaAPIError


# ================================================================
# _build_context
# ================================================================


class TestBuildContext:
    def _make_retriever(self):
        return Retriever(Mock(), Mock(), Mock(), Mock())

    def test_context_with_content(self):
        r = self._make_retriever()
        results = [
            SearchResult(doc_id="1", score=0.9, content="内容1", title="标题1"),
            SearchResult(doc_id="2", score=0.8, content="内容2", title="标题2"),
        ]
        context = r._build_context(results)
        assert context == ["内容1", "内容2"]

    def test_context_with_title_only(self):
        r = self._make_retriever()
        results = [
            SearchResult(doc_id="1", score=0.9, content="", title="标题1"),
        ]
        context = r._build_context(results)
        assert context == ["标题1"]

    def test_context_empty(self):
        r = self._make_retriever()
        context = r._build_context([])
        assert context == []

    def test_context_mixed(self):
        r = self._make_retriever()
        results = [
            SearchResult(doc_id="1", score=0.9, content="内容", title=""),
            SearchResult(doc_id="2", score=0.8, content="", title="标题"),
            SearchResult(doc_id="3", score=0.7, content="", title=""),
        ]
        context = r._build_context(results)
        assert len(context) == 2


# ================================================================
# _fallback_answer
# ================================================================


class TestFallbackAnswer:
    def _make_retriever(self):
        return Retriever(Mock(), Mock(), Mock(), Mock())

    def test_fallback_with_content(self):
        r = self._make_retriever()
        results = [
            SearchResult(doc_id="1", score=0.9, content="这是一段很长的内容..." * 10, title="标题1"),
        ]
        answer = r._fallback_answer(results)
        assert "标题1" in answer
        assert "..." in answer  # 长内容截断

    def test_fallback_short_content(self):
        r = self._make_retriever()
        results = [
            SearchResult(doc_id="1", score=0.9, content="简短内容", title="标题1"),
        ]
        answer = r._fallback_answer(results)
        assert "标题1" in answer
        assert "简短内容" in answer

    def test_fallback_no_title(self):
        r = self._make_retriever()
        results = [
            SearchResult(doc_id="1", score=0.9, content="内容", title=""),
        ]
        answer = r._fallback_answer(results)
        assert "内容" in answer

    def test_fallback_empty(self):
        r = self._make_retriever()
        answer = r._fallback_answer([])
        assert answer == "未找到相关信息"

    def test_fallback_max_3_sources(self):
        r = self._make_retriever()
        results = [SearchResult(doc_id=str(i), score=0.5, content="内容", title=f"标题{i}") for i in range(5)]
        answer = r._fallback_answer(results)
        # 最多包含前 3 个
        assert "标题1" in answer
        assert "标题4" not in answer


# ================================================================
# _check_handoff_needed
# ================================================================


class TestCheckHandoffNeeded:
    def _make_retriever(self):
        return Retriever(Mock(), Mock(), Mock(), Mock())

    def test_empty_query(self):
        r = self._make_retriever()
        assert r._check_handoff_needed([], "") is True
        assert r._check_handoff_needed([], "   ") is True

    def test_no_sources(self):
        r = self._make_retriever()
        assert r._check_handoff_needed([], "退货流程") is True

    def test_low_score(self):
        r = self._make_retriever()
        results = [SearchResult(doc_id="1", score=0.2, content="内容", title="标题")]
        assert r._check_handoff_needed(results, "问题") is True

    def test_good_score(self):
        r = self._make_retriever()
        results = [SearchResult(doc_id="1", score=0.9, content="内容", title="标题")]
        assert r._check_handoff_needed(results, "问题") is False

    def test_mixed_scores(self):
        r = self._make_retriever()
        results = [
            SearchResult(doc_id="1", score=0.9, content="内容", title="标题"),
            SearchResult(doc_id="2", score=0.2, content="内容", title="标题"),
        ]
        assert r._check_handoff_needed(results, "问题") is False  # max >= 0.35

    def test_none_score(self):
        r = self._make_retriever()
        results = [SearchResult(doc_id="1", score=None, content="内容", title="标题")]
        # max_score 为 0.0
        assert r._check_handoff_needed(results, "问题") is True


# ================================================================
# _encode_query 与 embedding_cache
# ================================================================


class TestEncodeQuery:
    @pytest.mark.asyncio
    async def test_encode_with_embedding_cache_hit(self):
        emb = Mock()
        emb.encode = AsyncMock(return_value=np.array([0.1, 0.2, 0.3]))
        emb_cache = Mock()
        emb_cache.aget = AsyncMock(return_value=np.array([0.5, 0.5, 0.5]))
        emb_cache.aset = AsyncMock()

        retriever = Retriever(emb, Mock(), Mock(), Mock(), embedding_cache=emb_cache)
        vec = await retriever._encode_query("测试")
        # 缓存命中，不调 encode
        assert np.array_equal(vec, np.array([0.5, 0.5, 0.5]))
        emb.encode.assert_not_called()

    @pytest.mark.asyncio
    async def test_encode_with_embedding_cache_miss(self):
        emb = Mock()
        emb.encode = AsyncMock(return_value=np.array([0.1, 0.2, 0.3]))
        emb_cache = Mock()
        emb_cache.aget = AsyncMock(return_value=None)
        emb_cache.aset = AsyncMock()

        retriever = Retriever(emb, Mock(), Mock(), Mock(), embedding_cache=emb_cache)
        vec = await retriever._encode_query("测试")
        assert np.array_equal(vec, np.array([0.1, 0.2, 0.3]))
        emb.encode.assert_called_once()
        emb_cache.aset.assert_called_once()

    @pytest.mark.asyncio
    async def test_encode_no_embedding_cache(self):
        emb = Mock()
        emb.encode = AsyncMock(return_value=np.array([0.1, 0.2, 0.3]))
        retriever = Retriever(emb, Mock(), Mock(), Mock(), embedding_cache=None)
        vec = await retriever._encode_query("测试")
        assert np.array_equal(vec, np.array([0.1, 0.2, 0.3]))
        emb.encode.assert_called_once()


# ================================================================
# _check_cache 与 _get_sources_for_cache 的同步降级
# ================================================================


class TestCacheDegradation:
    @pytest.mark.asyncio
    async def test_check_cache_sync_backend(self):
        """sync 缓存后端（无 aget 方法）降级走 get。"""
        emb = Mock()
        vector_store = Mock()
        # cache_backend 没有 aget 方法（同步）
        cache_backend = Mock()
        del cache_backend.aget
        cache_backend.get = Mock(return_value="缓存的回答")

        retriever = Retriever(emb, vector_store, cache_backend, Mock())
        result = await retriever._check_cache(np.array([0.1]), cache_backend=cache_backend)
        assert result == "缓存的回答"
        cache_backend.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_sources_sync_store(self):
        """sync vector store（无 asearch 方法）降级走 search。"""
        emb = Mock()
        vector_store = Mock()
        del vector_store.asearch
        vector_store.search = Mock(return_value=[SearchResult(doc_id="1", score=0.5, content="c", title="t")])

        retriever = Retriever(emb, vector_store, Mock(), Mock())
        sources = await retriever._get_sources_for_cache(np.array([0.1]), 5, "proj")
        assert len(sources) == 1
        vector_store.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_sources_failure(self):
        emb = Mock()
        vector_store = Mock()
        vector_store.asearch = AsyncMock(side_effect=Exception("store error"))

        retriever = Retriever(emb, vector_store, Mock(), Mock())
        sources = await retriever._get_sources_for_cache(np.array([0.1]), 5, "proj")
        assert sources == []


# ================================================================
# close / __aenter__ / __aexit__
# ================================================================


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_close(self):
        llm = Mock()
        llm.close = AsyncMock()
        vs = Mock()
        vs.aclose = AsyncMock()
        retriever = Retriever(Mock(), vs, Mock(), llm)
        await retriever.close()
        llm.close.assert_called_once()
        vs.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_no_llm_close(self):
        """LLM 客户端没有 close 方法时安全跳过。"""
        llm = Mock(spec=[])  # 无 close 方法
        # 移除 close 属性
        if hasattr(llm, "close"):
            del llm.close
        vs = Mock()
        vs.aclose = AsyncMock()
        retriever = Retriever(Mock(), vs, Mock(), llm)
        await retriever.close()
        vs.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_llm_close_error(self):
        llm = Mock()
        llm.close = AsyncMock(side_effect=Exception("close error"))
        vs = Mock()
        vs.aclose = AsyncMock()
        retriever = Retriever(Mock(), vs, Mock(), llm)
        await retriever.close()
        # 不应抛出异常

    @pytest.mark.asyncio
    async def test_close_sync_vector_store(self):
        """vector store 只有 sync close 方法时。"""
        llm = Mock()
        llm.close = AsyncMock()
        vs = Mock()
        del vs.aclose
        vs.close = Mock()
        retriever = Retriever(Mock(), vs, Mock(), llm)
        await retriever.close()
        vs.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        llm = Mock()
        llm.close = AsyncMock()
        vs = Mock()
        vs.aclose = AsyncMock()
        retriever = Retriever(Mock(), vs, Mock(), llm)
        async with retriever as r:
            assert r is retriever


# ================================================================
# RetrievalResult 扩展测试
# ================================================================


class TestRetrievalResultExtended:
    def test_handoff_suggested_default(self):
        r = RetrievalResult(answer="test", sources=[])
        assert r.handoff_suggested is False

    def test_handoff_suggested_true(self):
        r = RetrievalResult(answer="test", sources=[], handoff_suggested=True)
        assert r.handoff_suggested is True

    def test_repr(self):
        r = RetrievalResult(answer="这是回答内容", sources=[SearchResult(doc_id="1", score=0.9, content="c", title="t")])
        assert "这是回答内容" in repr(r)
        assert "sources=1" in repr(r)
        assert "cache_hit=False" in repr(r)

    def test_sources_returns_copy(self):
        sources = [SearchResult(doc_id="1", score=0.9, content="c", title="t")]
        r = RetrievalResult(answer="test", sources=sources)
        retrieved = r.sources
        assert retrieved == sources
        # 修改返回的列表不应影响内部
        retrieved.append(SearchResult(doc_id="2", score=0.5, content="c2", title="t2"))
        assert len(r.sources) == 1