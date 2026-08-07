"""重排序服务测试 — NoOpReranker、BGEM3Reranker（mock 模型）、create_reranker。"""

from unittest.mock import Mock, patch, MagicMock, AsyncMock

import pytest

from src.domain.models import SearchResult
from src.infrastructure.reranker import (
    BGEM3Reranker,
    NoOpReranker,
    create_reranker,
)


# ================================================================
# NoOpReranker
# ================================================================


class TestNoOpReranker:
    @pytest.fixture
    def reranker(self):
        return NoOpReranker()

    def test_is_enabled_false(self, reranker):
        assert reranker.is_enabled is False

    def test_rerank_returns_original(self, reranker):
        docs = [
            SearchResult(doc_id="1", score=0.9, content="a", title="A"),
            SearchResult(doc_id="2", score=0.8, content="b", title="B"),
        ]
        import asyncio
        result = asyncio.run(reranker.rerank("query", docs, top_k=5))
        assert len(result) == 2
        assert result[0].doc_id == "1"

    def test_rerank_respects_top_k(self, reranker):
        docs = [SearchResult(doc_id=str(i), score=0.5, content="x", title="X") for i in range(5)]
        import asyncio
        result = asyncio.run(reranker.rerank("query", docs, top_k=2))
        assert len(result) == 2

    def test_rerank_empty(self, reranker):
        import asyncio
        result = asyncio.run(reranker.rerank("query", [], top_k=5))
        assert result == []

    def test_close(self, reranker):
        reranker.close()  # 不应抛出异常

    @pytest.mark.asyncio
    async def test_rerank_async_empty(self, reranker):
        result = await reranker.rerank("query", [], top_k=5)
        assert result == []


# ================================================================
# BGEM3Reranker（mock 模型）
# ================================================================


class TestBGEM3RerankerMocked:
    def test_disabled_does_not_load_model(self):
        """禁用时跳过模型加载。"""
        with patch("src.infrastructure.reranker.settings") as mock_settings:
            mock_settings.reranker.enabled = False
            mock_settings.reranker.model_name = "test-model"
            mock_settings.reranker.device = "cpu"
            mock_settings.reranker.recall_top_k = 100
            mock_settings.reranker.rerank_top_k = 5
            with patch("sentence_transformers.CrossEncoder") as mock_ce:
                reranker = BGEM3Reranker(enabled=False)
                assert reranker._enabled is False
                assert reranker._model is None
                mock_ce.assert_not_called()

    def test_load_model_import_error(self):
        """sentence-transformers 未安装时优雅降级。"""
        with patch("src.infrastructure.reranker.settings") as mock_settings:
            mock_settings.reranker.enabled = True
            mock_settings.reranker.model_name = "test-model"
            mock_settings.reranker.device = "cpu"
            mock_settings.reranker.recall_top_k = 100
            mock_settings.reranker.rerank_top_k = 5
            with patch("sentence_transformers.CrossEncoder", side_effect=ImportError("no module")):
                reranker = BGEM3Reranker(enabled=True)
                assert reranker._enabled is False

    def test_load_model_other_error(self):
        """模型加载失败时优雅降级。"""
        with patch("src.infrastructure.reranker.settings") as mock_settings:
            mock_settings.reranker.enabled = True
            mock_settings.reranker.model_name = "test-model"
            mock_settings.reranker.device = "cpu"
            mock_settings.reranker.recall_top_k = 100
            mock_settings.reranker.rerank_top_k = 5
            with patch("sentence_transformers.CrossEncoder", side_effect=Exception("OOM")):
                reranker = BGEM3Reranker(enabled=True)
                assert reranker._enabled is False

    def test_rerank_sync_empty(self):
        """空文档列表直接返回空列表。"""
        with patch("src.infrastructure.reranker.settings") as mock_settings:
            mock_settings.reranker.enabled = True
            mock_settings.reranker.model_name = "test-model"
            mock_settings.reranker.device = "cpu"
            mock_settings.reranker.recall_top_k = 100
            mock_settings.reranker.rerank_top_k = 5

            reranker = BGEM3Reranker(enabled=True)
            reranker._model = Mock()  # 模拟已加载模型
            reranker._enabled = True

            result = reranker._rerank_sync("query", [], top_k=5)
            assert result == []

    def test_rerank_sync_returns_sorted(self):
        """重排序后按分数降序排列。"""
        with patch("src.infrastructure.reranker.settings") as mock_settings:
            mock_settings.reranker.enabled = True
            mock_settings.reranker.model_name = "test-model"
            mock_settings.reranker.device = "cpu"
            mock_settings.reranker.recall_top_k = 100
            mock_settings.reranker.rerank_top_k = 5

            reranker = BGEM3Reranker(enabled=True)
            reranker._model = Mock()
            # mock predict 返回分数
            reranker._model.predict = Mock(return_value=[0.1, 0.9, 0.5])
            reranker._enabled = True

            docs = [
                SearchResult(doc_id="1", score=0.3, content="a", title="A"),
                SearchResult(doc_id="2", score=0.3, content="b", title="B"),
                SearchResult(doc_id="3", score=0.3, content="c", title="C"),
            ]
            result = reranker._rerank_sync("query", docs, top_k=3)
            assert len(result) == 3
            assert result[0].doc_id == "2"  # 0.9 最高
            assert result[1].doc_id == "3"  # 0.5 次高
            assert result[2].doc_id == "1"  # 0.1 最低

    def test_rerank_respects_top_k(self):
        """返回 top_k 个结果。"""
        with patch("src.infrastructure.reranker.settings") as mock_settings:
            mock_settings.reranker.enabled = True
            mock_settings.reranker.model_name = "test-model"
            mock_settings.reranker.device = "cpu"
            mock_settings.reranker.recall_top_k = 100
            mock_settings.reranker.rerank_top_k = 5

            reranker = BGEM3Reranker(enabled=True)
            reranker._model = Mock()
            reranker._model.predict = Mock(return_value=[0.5, 0.4, 0.3, 0.2, 0.1])
            reranker._enabled = True

            docs = [SearchResult(doc_id=str(i), score=0.5, content="x", title="X") for i in range(5)]
            result = reranker._rerank_sync("query", docs, top_k=2)
            assert len(result) == 2

    @pytest.mark.asyncio
    async def test_rerank_disabled_returns_original(self):
        """禁用时直接返回原结果。"""
        reranker = BGEM3Reranker(enabled=False)
        docs = [SearchResult(doc_id="1", score=0.9, content="a", title="A")]
        result = await reranker.rerank("query", docs, top_k=5)
        assert len(result) == 1
        assert result[0].doc_id == "1"

    @pytest.mark.asyncio
    async def test_rerank_to_thread_fallback(self):
        """_rerank_sync 异常时降级返回原结果。"""
        with patch("src.infrastructure.reranker.settings") as mock_settings:
            mock_settings.reranker.enabled = True
            mock_settings.reranker.model_name = "test-model"
            mock_settings.reranker.device = "cpu"
            mock_settings.reranker.recall_top_k = 100
            mock_settings.reranker.rerank_top_k = 5

            reranker = BGEM3Reranker(enabled=True)
            reranker._model = Mock()
            reranker._enabled = True

            docs = [SearchResult(doc_id="1", score=0.9, content="a", title="A")]

            # 模拟 asyncio.to_thread 抛出异常
            with patch("src.infrastructure.reranker.asyncio.to_thread", side_effect=Exception("thread error")):
                result = await reranker.rerank("query", docs, top_k=5)
                assert len(result) == 1  # 降级返回原结果

    def test_close(self):
        with patch("src.infrastructure.reranker.settings") as mock_settings:
            mock_settings.reranker.enabled = True
            mock_settings.reranker.model_name = "test-model"
            mock_settings.reranker.device = "cpu"
            mock_settings.reranker.recall_top_k = 100
            mock_settings.reranker.rerank_top_k = 5

            reranker = BGEM3Reranker(enabled=True)
            reranker._model = Mock()
            reranker.close()
            assert reranker._model is None

    def test_close_no_model(self):
        reranker = BGEM3Reranker(enabled=False)
        reranker.close()  # 不应抛出异常

    def test_properties(self):
        with patch("src.infrastructure.reranker.settings") as mock_settings:
            mock_settings.reranker.enabled = True
            mock_settings.reranker.model_name = "test-model"
            mock_settings.reranker.device = "cpu"
            mock_settings.reranker.recall_top_k = 50
            mock_settings.reranker.rerank_top_k = 3
            with patch("sentence_transformers.CrossEncoder"):
                reranker = BGEM3Reranker(enabled=True)
                reranker._model = Mock()
                assert reranker.is_enabled is True
                assert reranker.recall_top_k == 50
                assert reranker.rerank_top_k == 3


# ================================================================
# create_reranker
# ================================================================


class TestCreateReranker:
    def test_create_reranker_enabled(self):
        with patch("src.infrastructure.reranker.settings") as mock_settings:
            mock_settings.reranker.enabled = True
            with patch("src.infrastructure.reranker.BGEM3Reranker") as mock_r:
                mock_r.return_value = Mock()
                result = create_reranker()
                mock_r.assert_called_once()

    def test_create_reranker_disabled(self):
        with patch("src.infrastructure.reranker.settings") as mock_settings:
            mock_settings.reranker.enabled = False
            result = create_reranker()
            assert isinstance(result, NoOpReranker)