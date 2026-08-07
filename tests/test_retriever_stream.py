"""Retriever 流式检索测试 — retrieve_stream 完整路径、fallback、__enter__/__exit__。"""

from unittest.mock import Mock, AsyncMock

import numpy as np
import pytest

from src.core.retriever import Retriever, RetrievalResult
from src.domain.models import SearchResult
from src.domain.exceptions import EmbeddingError, VectorStoreError, SenseNovaAPIError


def _make_mocks(stream_support=True):
    emb = Mock()
    emb.encode = AsyncMock(return_value=np.array([0.1, 0.2, 0.3], dtype=np.float32))
    vs = Mock()
    vs.asearch = AsyncMock(return_value=[
        SearchResult(doc_id="d1", score=0.9, content="相关内容", title="文档1"),
    ])
    cb = Mock()
    cb.aget = AsyncMock(return_value=None)
    cb.aset = AsyncMock(return_value=None)

    if stream_support:
        async def _stream_answer(query, context, **kwargs):
            yield {"type": "content", "content": "流式回答"}
            yield {"type": "content", "content": "继续"}

        llm = Mock()
        llm.stream_answer = _stream_answer
        llm.generate_answer = AsyncMock(return_value="完整回答")
    else:
        llm = Mock()
        llm.generate_answer = AsyncMock(return_value="完整回答")
        # 没有 stream_answer 方法

    return emb, vs, cb, llm


# ================================================================
# retrieve_stream 基本路径
# ================================================================


class TestRetrieveStream:
    @pytest.mark.asyncio
    async def test_empty_query(self):
        """空查询直接返回 error + done。"""
        emb, vs, cb, llm = _make_mocks()
        r = Retriever(emb, vs, cb, llm)
        events = []
        async for event in r.retrieve_stream(""):
            events.append(event)
        assert events[0]["event"] == "error"
        assert events[-1]["event"] == "done"

    @pytest.mark.asyncio
    async def test_embedding_error(self):
        """嵌入失败返回 error + done。"""
        emb, vs, cb, llm = _make_mocks()
        emb.encode.side_effect = EmbeddingError("嵌入失败")
        r = Retriever(emb, vs, cb, llm)
        events = []
        async for event in r.retrieve_stream("测试查询"):
            events.append(event)
        assert events[0]["event"] == "error"
        assert events[-1]["event"] == "done"

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """缓存命中直接返回。"""
        emb, vs, cb, llm = _make_mocks()
        cb.aget = AsyncMock(return_value="缓存的回答")
        r = Retriever(emb, vs, cb, llm)
        events = []
        async for event in r.retrieve_stream("测试查询"):
            events.append(event)
        # 应该有 sources 事件
        sources = [e for e in events if e["event"] == "sources"]
        assert len(sources) >= 1
        # 最后是 done
        assert events[-1]["event"] == "done"

    @pytest.mark.asyncio
    async def test_cache_hit_with_messages(self):
        """有消息时跳过缓存。"""
        emb, vs, cb, llm = _make_mocks()
        cb.aget = AsyncMock(return_value="缓存的回答")
        r = Retriever(emb, vs, cb, llm)
        events = []
        async for event in r.retrieve_stream("测试查询", messages=[{"role": "user", "content": "历史"}]):
            events.append(event)
        # 有消息时不走缓存，应该走 LLM
        answer_deltas = [e for e in events if e["event"] == "answer_delta"]
        assert len(answer_deltas) >= 1

    @pytest.mark.asyncio
    async def test_vector_search_error(self):
        """向量检索失败返回 error + done。"""
        emb, vs, cb, llm = _make_mocks()
        vs.asearch.side_effect = VectorStoreError("检索失败")
        r = Retriever(emb, vs, cb, llm)
        events = []
        async for event in r.retrieve_stream("测试查询"):
            events.append(event)
        assert events[0]["event"] == "error"
        assert events[-1]["event"] == "done"

    @pytest.mark.asyncio
    async def test_no_results(self):
        """无检索结果返回 handoff_suggested + answer。"""
        emb, vs, cb, llm = _make_mocks()
        vs.asearch = AsyncMock(return_value=[])
        r = Retriever(emb, vs, cb, llm)
        events = []
        async for event in r.retrieve_stream("测试查询"):
            events.append(event)
        handoff = [e for e in events if e["event"] == "handoff_suggested"]
        assert len(handoff) >= 1
        assert handoff[0]["data"] is True

    @pytest.mark.asyncio
    async def test_stream_success(self):
        """正常流式返回。"""
        emb, vs, cb, llm = _make_mocks(stream_support=True)
        r = Retriever(emb, vs, cb, llm)
        events = []
        async for event in r.retrieve_stream("测试查询"):
            events.append(event)
        answer_deltas = [e for e in events if e["event"] == "answer_delta"]
        assert len(answer_deltas) >= 1
        assert events[-1]["event"] == "done"

    @pytest.mark.asyncio
    async def test_stream_with_reranker(self):
        """启用重排序时使用 reranker。"""
        emb, vs, cb, llm = _make_mocks(stream_support=True)
        reranker = Mock()
        reranker.is_enabled = True
        reranker.recall_top_k = 20
        reranker.rerank = AsyncMock(return_value=[
            SearchResult(doc_id="d1", score=0.99, content="精排结果", title="d1"),
        ])
        r = Retriever(emb, vs, cb, llm, reranker=reranker)
        events = []
        async for event in r.retrieve_stream("测试查询"):
            events.append(event)
        answer_deltas = [e for e in events if e["event"] == "answer_delta"]
        assert len(answer_deltas) >= 1
        reranker.rerank.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_no_stream_support(self):
        """LLM 没有 stream_answer 方法时降级为 generate_answer。"""
        emb, vs, cb, llm = _make_mocks(stream_support=False)
        # 移除 stream_answer 属性（Mock 自动生成所有属性，需显式删除）
        if hasattr(llm, "stream_answer"):
            del llm.stream_answer
        r = Retriever(emb, vs, cb, llm)
        events = []
        async for event in r.retrieve_stream("测试查询"):
            events.append(event)
        answer_deltas = [e for e in events if e["event"] == "answer_delta"]
        assert len(answer_deltas) >= 1
        llm.generate_answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_llm_error(self):
        """LLM 流式失败降级为 fallback。"""
        emb, vs, cb, llm = _make_mocks(stream_support=True)
        llm.stream_answer = lambda **kwargs: (_ for _ in ()).throw(
            SenseNovaAPIError("LLM 错误")
        )
        r = Retriever(emb, vs, cb, llm)
        events = []
        async for event in r.retrieve_stream("测试查询"):
            events.append(event)
        answer_deltas = [e for e in events if e["event"] == "answer_delta"]
        assert len(answer_deltas) >= 1  # 降级后有 fallback 回答

    @pytest.mark.asyncio
    async def test_stream_with_reasoning(self):
        """流式包含推理链。"""
        emb, vs, cb, llm = _make_mocks(stream_support=True)

        async def _stream_with_reasoning(query, context, **kwargs):
            yield {"type": "reasoning", "content": "思考中..."}
            yield {"type": "content", "content": "最终回答"}

        llm.stream_answer = _stream_with_reasoning
        r = Retriever(emb, vs, cb, llm)
        events = []
        async for event in r.retrieve_stream("测试查询"):
            events.append(event)
        reasoning = [e for e in events if e["event"] == "reasoning_delta"]
        assert len(reasoning) >= 1


# ================================================================
# _record_stats
# ================================================================


class TestRecordStats:
    @pytest.mark.asyncio
    async def test_record_stats(self):
        """记录统计信息。"""
        stats = Mock()
        stats.record = Mock()
        r = Retriever(Mock(), Mock(), Mock(), Mock(), stats_registry=stats)
        r._record_stats("proj_1", cache_hit=False, prompt_tokens=10, completion_tokens=5)
        stats.record.assert_called_once_with(
            project_id="proj_1", prompt_tokens=10, completion_tokens=5, cache_hit=False
        )

    @pytest.mark.asyncio
    async def test_record_stats_no_registry(self):
        """无 stats_registry 时安全跳过。"""
        r = Retriever(Mock(), Mock(), Mock(), Mock(), stats_registry=None)
        r._record_stats("proj_1", cache_hit=False)  # 不应抛出异常

    @pytest.mark.asyncio
    async def test_record_stats_error(self):
        """stats_registry.record 失败时安全跳过。"""
        stats = Mock()
        stats.record.side_effect = Exception("记录失败")
        r = Retriever(Mock(), Mock(), Mock(), Mock(), stats_registry=stats)
        r._record_stats("proj_1", cache_hit=True)  # 不应抛出异常


# ================================================================
# _vector_search 同步降级
# ================================================================


class TestVectorSearch:
    @pytest.mark.asyncio
    async def test_vector_search_sync_fallback(self):
        """vector_store 无 asearch 时降级为 search。"""
        emb, vs, cb, llm = _make_mocks()
        del vs.asearch
        vs.search = Mock(return_value=[
            SearchResult(doc_id="d1", score=0.9, content="c", title="t"),
        ])
        r = Retriever(emb, vs, cb, llm)
        results = await r._vector_search(np.array([0.1, 0.2]), top_k=5, project_id="proj_1")
        assert len(results) == 1
        vs.search.assert_called_once()


# ================================================================
# __enter__ / __exit__
# ================================================================


class TestContextManager:
    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        llm = Mock()
        llm.close = AsyncMock()
        vs = Mock()
        vs.aclose = AsyncMock()
        r = Retriever(Mock(), vs, Mock(), llm)
        async with r as ret:
            assert ret is r
        llm.close.assert_awaited_once()
        vs.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_context_manager_with_sync_llm(self):
        """LLM 没有 close 方法时安全关闭。"""
        llm = Mock(spec=[])
        del llm.close
        vs = Mock()
        vs.aclose = AsyncMock()
        r = Retriever(Mock(), vs, Mock(), llm)
        async with r:
            pass
        vs.aclose.assert_awaited_once()

    def test_sync_context_manager(self):
        """同步上下文管理器调用 close。"""
        llm = Mock()
        llm.close = AsyncMock()
        vs = Mock()
        vs.aclose = AsyncMock()
        r = Retriever(Mock(), vs, Mock(), llm)
        # 同步 __exit__ 在存在事件循环时 try 走 loop.create_task
        # 验证不会抛出异常
        with r as ret:
            assert ret is r