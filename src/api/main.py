"""FastAPI 应用入口。

使用 lifespan 上下文管理器统一初始化共享组件，
通过 RetrieverFactory 按需为每个租户创建隔离的 Retriever 实例。

共享组件（一次启动，所有租户复用）：
  - EmbeddingService（SentenceBERT 模型）
  - ZvecStore（共享向量数据库，按 tenant_id 过滤实现隔离）
  - Reranker（BGE 模型）
  - EmbeddingCache（内存缓存）

租户隔离组件（按租户创建）：
  - LLMClient（每个租户可配置不同的 API Key / Base / Model）
  - LLMResponseCache（独立缓存目录，跨租户互不可见）
  - Retriever（组合上述组件）

支持优雅关闭：
- 监听 SIGTERM/SIGINT 信号
- 收到信号后停止接受新请求
- 等待正在处理的请求完成
- 关闭所有资源后退出
"""

import asyncio
import signal
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.core.factory import RetrieverFactory
from src.utils.limiter import limiter
from src.utils.dynamic_limiter import tenant_limiter

from src.api.routes import router, admin_router
from src.api.schemas import ErrorResponse
from src.domain.exceptions import (
    AppError,
    KnowledgeBaseError,
    DocumentNotFoundError,
    EmbeddingError,
    VectorStoreError,
    SenseNovaAPIError,
    MultiModalError,
)
from src.infrastructure.embedding_service import SentenceBertEmbeddingService
from src.infrastructure.embedding_cache import EmbeddingCache
from src.infrastructure.interfaces.embedding_service import EmbeddingService
from src.infrastructure.interfaces.vector_store import VectorStore
from src.infrastructure.interfaces.llm_client import LLMClient
from src.infrastructure.interfaces.reranker import Reranker
from src.infrastructure.reranker import create_reranker
from src.infrastructure.zvec_store import ZvecStore
from src.services.knowledge_service import KnowledgeService
from src.services.sensenova_client import SenseNovaClient
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

_shutdown_event = asyncio.Event()
_request_count = 0
_request_count_lock = asyncio.Lock()


def create_llm_client() -> LLMClient:
    """创建 LLM 客户端实例。

    使用 LLM_* 环境变量配置，兼容所有 OpenAI 格式的 API。
    只需修改 .env 中的 LLM_API_BASE / LLM_API_KEY / LLM_MODEL 即可切换：

    - OpenAI:    https://api.openai.com/v1
    - 通义千问:  https://dashscope.aliyuncs.com/compatible-mode/v1
    - DeepSeek:  https://api.deepseek.com
    - SenseNova: https://api.sensenova.cn/v1

    Returns:
        LLMClient: LLM 客户端实例
    """
    return SenseNovaClient()


def _signal_handler(signum, frame):
    """信号处理函数：触发优雅关闭。"""
    signal_name = signal.Signals(signum).name
    logger.info(f"收到 {signal_name} 信号，开始优雅关闭...")
    _shutdown_event.set()


async def _wait_for_pending_requests(timeout: int = 30) -> bool:
    """等待正在处理的请求完成。"""
    start_time = datetime.now()
    while (datetime.now() - start_time).total_seconds() < timeout:
        async with _request_count_lock:
            if _request_count == 0:
                logger.info("所有请求已处理完成")
                return True
        logger.info(f"等待剩余请求完成... 当前请求数: {_request_count}")
        await asyncio.sleep(0.5)
    logger.warning(f"等待请求超时 ({timeout}s)，仍有 {_request_count} 个请求")
    return False


async def _increment_request_count():
    """增加请求计数。"""
    async with _request_count_lock:
        global _request_count
        _request_count += 1


async def _decrement_request_count():
    """减少请求计数。"""
    async with _request_count_lock:
        global _request_count
        _request_count -= 1


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：统一初始化和清理组件，支持优雅关闭。

    共享组件（全局单例）：
      - EmbeddingService（SentenceBERT 模型）
      - ZvecStore（共享向量数据库，按 tenant_id filter 隔离）
      - Reranker（BGE 模型）
      - EmbeddingCache（内存缓存）
      - KnowledgeService（文档 CRUD）

    租户隔离组件（通过 RetrieverFactory 按需创建）：
      - LLMClient（每个租户可配置独立 API Key / Base / Model）
      - LLMResponseCache（独立缓存目录，跨租户互不可见）
      - Retriever（组合上述组件）
    """
    logger.info("正在初始化应用组件...")

    try:
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)
        logger.info("信号处理器已注册")

        embedding_service: EmbeddingService = SentenceBertEmbeddingService()
        logger.info("EmbeddingService 初始化完成")

        vector_store: VectorStore = ZvecStore()
        logger.info("ZvecStore 初始化完成")

        embedding_cache = EmbeddingCache()
        logger.info(
            f"EmbeddingCache 初始化完成 "
            f"(enabled={embedding_cache.stats()['enabled']}, "
            f"maxsize={embedding_cache.stats()['maxsize']})"
        )

        llm_client: LLMClient = create_llm_client()
        logger.info("LLMClient 初始化完成")

        reranker: Reranker = create_reranker()
        logger.info(f"Reranker 初始化完成 (启用: {reranker.is_enabled})")

        from src.services.tenant_stats import TenantStatsRegistry

        retriever_factory = RetrieverFactory(
            embedding_service=embedding_service,
            vector_store=vector_store,
            default_llm_client=llm_client,
            reranker=reranker,
            embedding_cache=embedding_cache,
            stats_registry=TenantStatsRegistry(),
        )
        logger.info("RetrieverFactory 初始化完成")
        app.state.stats_registry = retriever_factory._stats_registry

        # 向后兼容：保留全局单例 retriever（用于 health check 等非租户路径）
        retriever = retriever_factory.get_retriever_for_tenant("default")
        logger.info("Retriever（default）初始化完成")

        knowledge_service = KnowledgeService(
            vector_store=vector_store,
            embedding_service=embedding_service,
        )
        logger.info("KnowledgeService 初始化完成")

        # 确保默认租户存在（向后兼容单租户遗留数据）
        from src.services.tenant_service import TenantService

        tenant_svc = TenantService()
        default_tenant = tenant_svc.ensure_default_tenant()
        logger.info(
            f"默认租户已确保存在: {default_tenant.tenant_id} ({default_tenant.name})"
        )

        app.state.embedding_service = embedding_service
        app.state.vector_store = vector_store
        app.state.retriever_factory = retriever_factory
        app.state.retriever = retriever  # 向后兼容
        app.state.reranker = reranker
        app.state.knowledge_service = knowledge_service
        app.state.tenant_service = tenant_svc

        logger.info("所有组件初始化完成")
        yield
    except Exception as e:
        logger.error(f"组件初始化失败: {e}", exc_info=True)
        raise
    finally:
        logger.info("正在清理应用资源...")

        if _shutdown_event.is_set():
            logger.info("等待正在处理的请求完成...")
            await _wait_for_pending_requests()

        if hasattr(app.state, "retriever_factory"):
            try:
                app.state.retriever_factory.close()
                logger.info("RetrieverFactory 已关闭")
            except Exception as e:
                logger.error(f"关闭 RetrieverFactory 失败: {e}")

        if hasattr(app.state, "reranker"):
            try:
                app.state.reranker.close()
                logger.info("Reranker 已关闭")
            except Exception as e:
                logger.error(f"关闭 Reranker 失败: {e}")

        if hasattr(app.state, "knowledge_service"):
            try:
                await app.state.knowledge_service.close()
                logger.info("KnowledgeService 已关闭")
            except Exception as e:
                logger.error(f"关闭 KnowledgeService 失败: {e}")

        logger.info("资源清理完成")


app = FastAPI(
    title=settings.app_name,
    description="OpenAsk - 基于 Zvec 向量数据库的智能客服问答系统 API",
    version="1.0.0",
    lifespan=lifespan,
    debug=settings.debug,
)

app.state.limiter = limiter
app.state.tenant_limiter = tenant_limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def dynamic_rate_limit_middleware(request: Request, call_next):
    """动态限流中间件：按租户 API Key 限流，支持运行时配置。

    优先从 request.state.tenant 获取租户上下文（由 resolve_tenant 注入），
    无租户时按 IP 限流。
    """
    tenant = getattr(request.state, "tenant", None)
    if tenant and not tenant_limiter.is_allowed(request, tenant):
        return JSONResponse(
            status_code=429,
            content={
                "error": "RateLimitExceeded",
                "detail": f"Rate limit exceeded: {tenant.rate_limit_per_user}",
                "retry_after": 60,
            },
            headers={
                "X-RateLimit-Limit": tenant.rate_limit_per_user,
                "X-RateLimit-Remaining": str(tenant_limiter.get_remaining(request, tenant)),
            },
        )
    return await call_next(request)


@app.middleware("http")
async def request_count_middleware(request: Request, call_next):
    """请求计数中间件：跟踪正在处理的请求数，支持优雅关闭。"""
    if _shutdown_event.is_set():
        return JSONResponse(
            status_code=503,
            content={
                "error": "ServiceUnavailable",
                "message": "服务正在关闭，请稍后重试",
                "timestamp": datetime.now().isoformat(),
            },
        )

    await _increment_request_count()
    try:
        response = await call_next(request)
    finally:
        await _decrement_request_count()
    return response


app.include_router(router)
app.include_router(admin_router)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    """全局应用异常处理器。"""
    status_code = 500
    if isinstance(exc, (KnowledgeBaseError, MultiModalError)):
        status_code = 400
    elif isinstance(exc, DocumentNotFoundError):
        status_code = 404
    elif isinstance(exc, (EmbeddingError, VectorStoreError, SenseNovaAPIError)):
        status_code = 503

    logger.error(f"应用异常: {exc}", exc_info=True)

    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=exc.__class__.__name__,
            message=str(exc),
            timestamp=datetime.now(),
        ).model_dump(mode="json"),
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    """全局通用异常处理器。"""
    logger.error(f"未处理异常: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="InternalServerError",
            message="服务器内部错误，请稍后重试",
            timestamp=datetime.now(),
        ).model_dump(mode="json"),
    )