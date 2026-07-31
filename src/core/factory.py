"""Retriever 工厂：按租户创建隔离的 Retriever 实例。

每个租户获得一组隔离组件：
  - LLMResponseCache（独立缓存目录，跨租户互不可见）
  - LLMClient（使用租户自有 LLM 配置，或全局降级）
  - 租户自定义 system_prompt（注入 retriever）

共享组件（全局单例，由 main.py lifespan 注入）：
  - EmbeddingService（SentenceBERT 模型加载代价高，全局共享）
  - ZvecStore（共享数据库，通过 tenant_id filter_expr 实现隔离）
  - Reranker（BGE 模型加载代价高，全局共享）
  - EmbeddingCache（内存缓存，全局共享）
"""

import os
import threading
from typing import Optional

from src.core.retriever import Retriever
from src.domain.models import DEFAULT_TENANT_ID, Tenant
from src.services.tenant_stats import TenantStatsRegistry
from src.infrastructure.interfaces.cache_backend import CacheBackend
from src.infrastructure.interfaces.embedding_service import EmbeddingService
from src.infrastructure.interfaces.llm_client import LLMClient
from src.infrastructure.interfaces.reranker import Reranker
from src.infrastructure.interfaces.vector_store import VectorStore
from src.infrastructure.llm_response_cache import LLMResponseCache
from src.services.sensenova_client import SenseNovaClient
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


class RetrieverCache:
    """LRU 风格的 Retriever 实例缓存，避免重复创建。"""

    def __init__(self, maxsize: int = 128):
        self._cache: dict[str, Retriever] = {}
        self._maxsize = maxsize
        self._lock = threading.RLock()

    def get(self, tenant_id: str) -> Optional[Retriever]:
        with self._lock:
            return self._cache.get(tenant_id)

    def put(self, tenant_id: str, retriever: Retriever) -> None:
        with self._lock:
            if tenant_id not in self._cache and len(self._cache) >= self._maxsize:
                oldest = next(iter(self._cache))
                self._cache.pop(oldest)
                logger.warning(
                    f"Retriever 缓存已达上限 {self._maxsize}，驱逐: {oldest}"
                )
            self._cache[tenant_id] = retriever

    async def close(self) -> None:
        with self._lock:
            for tenant_id, retriever in list(self._cache.items()):
                try:
                    await retriever.close()
                    logger.info(f"Retriever 已关闭: tenant={tenant_id}")
                except Exception as e:
                    logger.warning(f"关闭 Retriever 失败: tenant={tenant_id} {e}")
            self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


class RetrieverFactory:
    """按租户创建/获取隔离的 Retriever 实例。

    使用方式：
        factory = app.state.retriever_factory
        retriever = factory.get_retriever_for_tenant(tenant_id, tenant)
        result = await retriever.retrieve(query, tenant_id=tenant.tenant_id)

    Args:
        tenant: 租户对象，携带 LLM 配置（可选）。
                若提供且租户配置了独立 LLM API Key，使用该配置；
                否则使用全局默认配置。
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        vector_store: VectorStore,
        default_llm_client: LLMClient,
        embedding_cache=None,
        reranker: Optional[Reranker] = None,
        lru_maxsize: int = 128,
        stats_registry: Optional[TenantStatsRegistry] = None,
    ):
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._default_llm_client = default_llm_client
        self._reranker = reranker
        self._embedding_cache = embedding_cache
        self._stats_registry = stats_registry
        self._cache = RetrieverCache(maxsize=lru_maxsize)

    def get_retriever_for_tenant(
        self, tenant_id: str, tenant: Optional[Tenant] = None
    ) -> Retriever:
        """获取/创建租户专属的 Retriever 实例。

        缓存复用：同一 tenant_id 共享一个 Retriever 实例。
        隔离：每个 Retriever 绑定独立的 LLM 响应缓存。
        LLM 配置：若 tenant 配置了独立 LLM API Key，使用该配置；
                  否则使用全局默认配置。
        """
        existing = self._cache.get(tenant_id)
        if existing is not None:
            return existing

        cache_backend = self._create_cache_for_tenant(tenant_id)
        llm_client = self._create_llm_client_for_tenant(tenant_id, tenant)

        retriever = Retriever(
            embedding_service=self._embedding_service,
            vector_store=self._vector_store,
            cache_backend=cache_backend,
            llm_client=llm_client,
            reranker=self._reranker,
            embedding_cache=self._embedding_cache,
            stats_registry=self._stats_registry,
        )

        self._cache.put(tenant_id, retriever)
        logger.info(f"Retriever 实例已创建: tenant={tenant_id}")
        return retriever

    def close(self) -> None:
        """关闭所有租户 Retriever 实例（共享组件由 main.py 统一关闭）。"""
        self._cache.close()
        logger.info("RetrieverFactory 已关闭")

    # ------------------------------------------------------------------
    # 内部工厂方法
    # ------------------------------------------------------------------

    def _create_cache_for_tenant(self, tenant_id: str) -> LLMResponseCache:
        """为租户创建隔离的 LLM 响应缓存。"""
        if tenant_id == DEFAULT_TENANT_ID:
            return LLMResponseCache()

        cache_base = settings.zvec.cache_path or "data/zvec_llm_cache"
        cache_path = os.path.join(cache_base, tenant_id)
        cache = LLMResponseCache(cache_path=cache_path)
        logger.info(
            f"LLM 缓存实例已创建: tenant={tenant_id} path={cache_path}"
        )
        return cache

    def _create_llm_client_for_tenant(
        self, tenant_id: str, tenant: Optional[Tenant] = None
    ) -> LLMClient:
        """为租户创建 LLM 客户端。

        优先使用租户自定义配置（API Key / Base / Model），
        降级使用全局默认配置。
        """
        if tenant_id == DEFAULT_TENANT_ID:
            return self._default_llm_client

        if tenant is not None and tenant.llm_api_key:
            logger.info(
                f"租户 {tenant_id} 使用自定义 LLM 配置 "
                f"(base={tenant.llm_api_base[:30] if tenant.llm_api_base else 'default'}, "
                f"model={tenant.llm_model or 'default'})"
            )
            return SenseNovaClient(
                api_key=tenant.llm_api_key,
                api_base=tenant.llm_api_base or None,
                model=tenant.llm_model or None,
                timeout=tenant.llm_timeout or settings.llm.timeout,
            )

        logger.info(f"租户 {tenant_id} 使用全局默认 LLM 配置")
        return self._default_llm_client
