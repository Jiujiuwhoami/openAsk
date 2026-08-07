"""RetrieverFactory 测试 — 缓存、创建、隔离、LLM 配置。"""

from unittest.mock import Mock, AsyncMock, patch

import pytest

from src.core.factory import RetrieverFactory, RetrieverCache
from src.domain.project import Project
from src.domain.models import DEFAULT_PROJECT_ID


# ================================================================
# RetrieverCache
# ================================================================


class TestRetrieverCache:
    @pytest.fixture
    def cache(self):
        return RetrieverCache(maxsize=3)

    def test_get_miss(self, cache):
        assert cache.get("proj_1") is None

    def test_put_and_get(self, cache):
        r = Mock()
        cache.put("proj_1", r)
        assert cache.get("proj_1") is r

    def test_len(self, cache):
        cache.put("a", Mock())
        cache.put("b", Mock())
        assert len(cache) == 2

    def test_eviction(self, cache):
        """超过 maxsize 时驱逐最旧条目。"""
        for i in range(4):
            cache.put(f"proj_{i}", Mock())
        assert len(cache) == 3
        # 最旧的 proj_0 已被驱逐
        assert cache.get("proj_0") is None

    @pytest.mark.asyncio
    async def test_close(self, cache):
        r1 = Mock()
        r1.close = AsyncMock()
        r2 = Mock()
        r2.close = AsyncMock()
        cache.put("a", r1)
        cache.put("b", r2)

        await cache.close()
        assert len(cache) == 0
        r1.close.assert_awaited_once()
        r2.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_with_error(self, cache):
        r = Mock()
        r.close = AsyncMock(side_effect=Exception("close error"))
        cache.put("a", r)
        await cache.close()  # 不应抛出异常
        assert len(cache) == 0


# ================================================================
# RetrieverFactory
# ================================================================


class TestRetrieverFactory:
    @pytest.fixture
    def factory(self):
        emb = Mock()
        vs = Mock()
        llm = Mock()
        f = RetrieverFactory(
            embedding_service=emb,
            vector_store=vs,
            default_llm_client=llm,
            embedding_cache=Mock(),
            reranker=Mock(),
            lru_maxsize=10,
            stats_registry=Mock(),
        )
        # 默认 mock 掉内部创建方法（避免文件系统访问）
        f._create_cache_for_project = Mock(return_value=Mock())
        f._create_llm_client_for_project = Mock(return_value=Mock())
        return f

    def test_get_retriever_caches(self, factory):
        """同一 project_id 复用实例。"""
        r1 = factory.get_retriever_for_project("proj_1")
        r2 = factory.get_retriever_for_project("proj_1")
        assert r1 is r2

    def test_get_retriever_different(self, factory):
        """不同 project_id 获取不同实例。"""
        r1 = factory.get_retriever_for_project("proj_a")
        r2 = factory.get_retriever_for_project("proj_b")
        assert r1 is not r2

    def test_get_retriever_default_project(self, factory):
        """默认项目使用默认 LLM 客户端。"""
        r = factory.get_retriever_for_project(DEFAULT_PROJECT_ID)
        assert r is not None

    def test_get_retriever_with_llm_config(self, factory):
        """项目有自定义 LLM 配置时使用独立客户端。"""
        # 恢复真实的 _create_llm_client_for_project
        factory._create_cache_for_project = Mock(return_value=Mock())
        del factory._create_llm_client_for_project

        project = Mock(spec=Project)
        project.project_id = "proj_custom"
        project.llm_api_key = "sk-custom"
        project.llm_api_base = "https://custom.api.com"
        project.llm_model = "gpt-4"
        project.llm_timeout = 60

        with patch("src.core.factory.SenseNovaClient") as mock_sn:
            mock_sn.return_value = Mock()
            r = factory.get_retriever_for_project("proj_custom", project)
            assert r is not None
            mock_sn.assert_called_once_with(
                api_key="sk-custom",
                api_base="https://custom.api.com",
                model="gpt-4",
                timeout=60,
            )

    def test_get_retriever_without_llm_config(self, factory):
        """项目无自定义 LLM 配置时使用全局默认。"""
        factory._create_cache_for_project = Mock(return_value=Mock())
        del factory._create_llm_client_for_project

        project = Mock(spec=Project)
        project.project_id = "proj_default"
        project.llm_api_key = ""

        with patch("src.core.factory.SenseNovaClient") as mock_sn:
            r = factory.get_retriever_for_project("proj_default", project)
            assert r is not None
            mock_sn.assert_not_called()

    def test_get_retriever_none_project(self, factory):
        """project 为 None 时使用全局默认 LLM。"""
        r = factory.get_retriever_for_project("proj_no_project", None)
        assert r is not None

    @pytest.mark.asyncio
    async def test_close(self, factory):
        factory.get_retriever_for_project("proj_1")
        factory.get_retriever_for_project("proj_2")

        factory._cache = Mock()
        factory._cache.close = AsyncMock()
        await factory.close()
        factory._cache.close.assert_awaited_once()


class TestCreateCacheForProject:
    def test_create_cache_default(self, factory):
        """默认项目使用默认缓存路径。"""
        with patch("src.core.factory.LLMResponseCache") as mock_cache:
            cache = factory._create_cache_for_project(DEFAULT_PROJECT_ID)
            mock_cache.assert_called_once()

    def test_create_cache_custom(self, factory):
        """非默认项目使用独立缓存路径。"""
        with patch("src.core.factory.LLMResponseCache") as mock_cache:
            with patch("src.core.factory.settings") as mock_settings:
                mock_settings.zvec.cache_path = "data/test_cache"
                cache = factory._create_cache_for_project("proj_custom")
                mock_cache.assert_called_once_with(cache_path="data/test_cache/proj_custom")

    @pytest.fixture
    def factory(self):
        emb = Mock()
        vs = Mock()
        llm = Mock()
        return RetrieverFactory(
            embedding_service=emb,
            vector_store=vs,
            default_llm_client=llm,
        )