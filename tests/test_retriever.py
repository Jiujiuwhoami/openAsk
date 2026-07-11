"""检索引擎测试。"""

import numpy as np
import pytest
from unittest.mock import Mock, AsyncMock

from src.core.retriever import Retriever, RetrievalResult
from src.domain.models import SearchResult
from src.domain.exceptions import EmbeddingError, VectorStoreError, SenseNovaAPIError


class TestRetrievalResult:
    """检索结果值对象测试。"""

    def test_equality(self):
        """测试相等性判断。"""
        result1 = RetrievalResult(
            answer="测试回答",
            sources=[SearchResult(doc_id="1", score=0.9, content="内容")],
            cache_hit=True,
        )
        result2 = RetrievalResult(
            answer="测试回答",
            sources=[SearchResult(doc_id="1", score=0.9, content="内容")],
            cache_hit=True,
        )
        result3 = RetrievalResult(
            answer="不同回答",
            sources=[SearchResult(doc_id="1", score=0.9, content="内容")],
            cache_hit=True,
        )

        assert result1 == result2
        assert result1 != result3
        assert result1 != "not a RetrievalResult"


class TestRetriever:
    """检索引擎测试。"""

    def _create_mocks(self, with_reranker: bool = False):
        """创建依赖组件的 Mock 对象。"""
        embedding_service = Mock()
        embedding_service.encode = AsyncMock(
            return_value=np.array([0.1, 0.2, 0.3], dtype=np.float32)
        )

        vector_store = Mock()
        vector_store.asearch = AsyncMock(return_value=[
            SearchResult(
                doc_id="doc1",
                score=0.95,
                content="这是相关文档内容",
                title="相关文档标题",
            ),
            SearchResult(
                doc_id="doc2",
                score=0.85,
                content="另一篇相关文档",
                title="文档标题2",
            ),
        ])

        cache_backend = Mock()
        cache_backend.aget = AsyncMock(return_value=None)
        cache_backend.aset = AsyncMock(return_value=None)

        llm_client = Mock()
        llm_client.generate_answer = AsyncMock(
            return_value="这是基于文档生成的回答"
        )

        if with_reranker:
            reranker = Mock()
            reranker.is_enabled = True
            reranker.recall_top_k = 20
            reranker.rerank = AsyncMock(
                return_value=[
                    SearchResult(
                        doc_id="doc1",
                        score=0.99,
                        content="这是相关文档内容",
                        title="相关文档标题",
                    ),
                ]
            )
            return embedding_service, vector_store, cache_backend, llm_client, reranker

        return embedding_service, vector_store, cache_backend, llm_client, None

    @pytest.mark.asyncio
    async def test_empty_query(self):
        """测试空查询。"""
        embedding_service, vector_store, cache_backend, llm_client, _ = self._create_mocks()
        retriever = Retriever(embedding_service, vector_store, cache_backend, llm_client)

        result = await retriever.retrieve("")

        assert result.answer == "请提供有效的查询内容"
        assert result.sources == []
        assert not result.cache_hit
        assert not result.llm_used
        assert not result.reranked

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """测试缓存命中。"""
        embedding_service, vector_store, cache_backend, llm_client, _ = self._create_mocks()
        cache_backend.aget.return_value = "缓存的回答"

        retriever = Retriever(embedding_service, vector_store, cache_backend, llm_client)
        result = await retriever.retrieve("测试查询")

        assert result.answer == "缓存的回答"
        assert len(result.sources) == 2
        assert result.cache_hit
        assert not result.llm_used
        assert not result.reranked
        llm_client.generate_answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        """测试缓存未命中，正常流程。"""
        embedding_service, vector_store, cache_backend, llm_client, _ = self._create_mocks()
        cache_backend.aget.return_value = None

        retriever = Retriever(embedding_service, vector_store, cache_backend, llm_client)
        result = await retriever.retrieve("测试查询")

        assert result.answer == "这是基于文档生成的回答"
        assert len(result.sources) == 2
        assert not result.cache_hit
        assert result.llm_used
        assert not result.reranked
        llm_client.generate_answer.assert_called_once()
        cache_backend.aset.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_failure_degrade(self):
        """测试缓存查询失败，降级为直接检索。"""
        embedding_service, vector_store, cache_backend, llm_client, _ = self._create_mocks()
        cache_backend.aget.side_effect = Exception("缓存服务不可用")

        retriever = Retriever(embedding_service, vector_store, cache_backend, llm_client)
        result = await retriever.retrieve("测试查询")

        assert result.answer == "这是基于文档生成的回答"
        assert not result.cache_hit
        assert result.llm_used
        assert not result.reranked

    @pytest.mark.asyncio
    async def test_embedding_failure(self):
        """测试嵌入失败。"""
        embedding_service, vector_store, cache_backend, llm_client, _ = self._create_mocks()
        embedding_service.encode.side_effect = EmbeddingError("嵌入服务失败")

        retriever = Retriever(embedding_service, vector_store, cache_backend, llm_client)
        result = await retriever.retrieve("测试查询")

        assert result.answer == "系统暂无法处理该查询，请稍后重试"
        assert result.sources == []
        assert not result.cache_hit
        assert not result.llm_used
        assert not result.reranked

    @pytest.mark.asyncio
    async def test_vector_search_failure(self):
        """测试向量检索失败。"""
        embedding_service, vector_store, cache_backend, llm_client, _ = self._create_mocks()
        cache_backend.aget.return_value = None
        vector_store.asearch.side_effect = VectorStoreError("向量库不可用")

        retriever = Retriever(embedding_service, vector_store, cache_backend, llm_client)
        result = await retriever.retrieve("测试查询")

        assert result.answer == "无法检索到相关信息，请稍后重试"
        assert result.sources == []
        assert not result.cache_hit
        assert not result.llm_used
        assert not result.reranked

    @pytest.mark.asyncio
    async def test_no_search_results(self):
        """测试未检索到任何文档。"""
        embedding_service, vector_store, cache_backend, llm_client, _ = self._create_mocks()
        cache_backend.aget.return_value = None
        vector_store.asearch.return_value = []

        retriever = Retriever(embedding_service, vector_store, cache_backend, llm_client)
        result = await retriever.retrieve("测试查询")

        assert result.answer == "未找到相关信息"
        assert result.sources == []
        assert not result.cache_hit
        assert not result.llm_used
        assert not result.reranked

    @pytest.mark.asyncio
    async def test_llm_failure_degrade(self):
        """测试 LLM 调用失败，降级为返回原始文档。"""
        embedding_service, vector_store, cache_backend, llm_client, _ = self._create_mocks()
        cache_backend.aget.return_value = None
        llm_client.generate_answer.side_effect = SenseNovaAPIError("LLM 服务不可用")

        retriever = Retriever(embedding_service, vector_store, cache_backend, llm_client)
        result = await retriever.retrieve("测试查询")

        assert "相关文档内容" in result.answer
        assert "文档标题2" in result.answer
        assert len(result.sources) == 2
        assert not result.cache_hit
        assert result.llm_used
        assert not result.reranked

    @pytest.mark.asyncio
    async def test_llm_exception_degrade(self):
        """测试 LLM 调用抛出未知异常，降级为返回原始文档。"""
        embedding_service, vector_store, cache_backend, llm_client, _ = self._create_mocks()
        cache_backend.aget.return_value = None
        llm_client.generate_answer.side_effect = Exception("网络超时")

        retriever = Retriever(embedding_service, vector_store, cache_backend, llm_client)
        result = await retriever.retrieve("测试查询")

        assert "相关文档内容" in result.answer
        assert len(result.sources) == 2
        assert not result.reranked

    @pytest.mark.asyncio
    async def test_cache_write_failure(self):
        """测试缓存写入失败（不影响主流程）。"""
        embedding_service, vector_store, cache_backend, llm_client, _ = self._create_mocks()
        cache_backend.aget.return_value = None
        cache_backend.aset.side_effect = Exception("缓存写入失败")

        retriever = Retriever(embedding_service, vector_store, cache_backend, llm_client)
        result = await retriever.retrieve("测试查询")

        assert result.answer == "这是基于文档生成的回答"
        assert result.llm_used
        assert not result.reranked

    @pytest.mark.asyncio
    async def test_rerank_enabled(self):
        """测试重排序启用。"""
        embedding_service, vector_store, cache_backend, llm_client, reranker = self._create_mocks(
            with_reranker=True
        )
        cache_backend.aget.return_value = None

        retriever = Retriever(
            embedding_service, vector_store, cache_backend, llm_client, reranker=reranker
        )
        result = await retriever.retrieve("测试查询", top_k=1)

        assert result.answer == "这是基于文档生成的回答"
        assert len(result.sources) == 1
        assert result.reranked
        reranker.rerank.assert_called_once()

    @pytest.mark.asyncio
    async def test_rerank_failure_degrade(self):
        """测试重排序失败，降级为不重排序。"""
        embedding_service, vector_store, cache_backend, llm_client, reranker = self._create_mocks(
            with_reranker=True
        )
        cache_backend.aget.return_value = None
        reranker.rerank.side_effect = Exception("重排序服务不可用")

        retriever = Retriever(
            embedding_service, vector_store, cache_backend, llm_client, reranker=reranker
        )
        result = await retriever.retrieve("测试查询")

        assert result.answer == "这是基于文档生成的回答"
        assert len(result.sources) == 2
        assert not result.reranked

    @pytest.mark.asyncio
    async def test_rerank_disabled(self):
        """测试重排序未启用。"""
        embedding_service, vector_store, cache_backend, llm_client, reranker = self._create_mocks(
            with_reranker=True
        )
        reranker.is_enabled = False
        cache_backend.aget.return_value = None

        retriever = Retriever(
            embedding_service, vector_store, cache_backend, llm_client, reranker=reranker
        )
        result = await retriever.retrieve("测试查询")

        assert result.answer == "这是基于文档生成的回答"
        assert len(result.sources) == 2
        assert not result.reranked
        reranker.rerank.assert_not_called()