"""FastAPI 应用入口。

使用 lifespan 上下文管理器统一初始化所有组件单例，
确保 Retriever 和 KnowledgeService 共享同一组底层实例。
"""

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.utils.limiter import limiter

from src.api.routes import router
from src.api.schemas import ErrorResponse
from src.core.retriever import Retriever
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
from src.infrastructure.interfaces.embedding_service import EmbeddingService
from src.infrastructure.interfaces.vector_store import VectorStore
from src.infrastructure.interfaces.cache_backend import CacheBackend
from src.infrastructure.interfaces.reranker import Reranker
from src.infrastructure.llm_response_cache import LLMResponseCache
from src.infrastructure.reranker import create_reranker
from src.infrastructure.zvec_store import ZvecStore
from src.services.knowledge_service import KnowledgeService
from src.services.sensenova_client import SenseNovaClient
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：统一初始化和清理组件。"""
    logger.info("正在初始化应用组件...")

    try:
        embedding_service: EmbeddingService = SentenceBertEmbeddingService()
        logger.info("EmbeddingService 初始化完成")

        vector_store: VectorStore = ZvecStore()
        logger.info("ZvecStore 初始化完成")

        cache_backend: CacheBackend = LLMResponseCache()
        logger.info("LLMResponseCache 初始化完成")

        llm_client = SenseNovaClient()
        logger.info("SenseNovaClient 初始化完成")

        reranker: Reranker = create_reranker()
        logger.info(f"Reranker 初始化完成 (启用: {reranker.is_enabled})")

        retriever = Retriever(
            embedding_service=embedding_service,
            vector_store=vector_store,
            cache_backend=cache_backend,
            llm_client=llm_client,
            reranker=reranker,
        )
        logger.info("Retriever 初始化完成")

        knowledge_service = KnowledgeService(
            vector_store=vector_store,
            embedding_service=embedding_service,
        )
        logger.info("KnowledgeService 初始化完成")

        app.state.embedding_service = embedding_service
        app.state.vector_store = vector_store
        app.state.cache_backend = cache_backend
        app.state.llm_client = llm_client
        app.state.reranker = reranker
        app.state.retriever = retriever
        app.state.knowledge_service = knowledge_service

        logger.info("所有组件初始化完成")
        yield
    except Exception as e:
        logger.error(f"组件初始化失败: {e}", exc_info=True)
        raise
    finally:
        logger.info("正在清理应用资源...")
        if hasattr(app.state, "retriever"):
            try:
                await app.state.retriever.close()
                logger.info("Retriever 已关闭")
            except Exception as e:
                logger.error(f"关闭 Retriever 失败: {e}")
        if hasattr(app.state, "reranker"):
            try:
                app.state.reranker.close()
                logger.info("Reranker 已关闭")
            except Exception as e:
                logger.error(f"关闭 Reranker 失败: {e}")
        if hasattr(app.state, "knowledge_service"):
            try:
                app.state.knowledge_service.close()
                logger.info("KnowledgeService 已关闭")
            except Exception as e:
                logger.error(f"关闭 KnowledgeService 失败: {e}")
        logger.info("资源清理完成")


app = FastAPI(
    title=settings.app_name,
    description="基于 Zvec 向量数据库的智能客服问答系统 API",
    version="1.0.0",
    lifespan=lifespan,
    debug=settings.debug,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


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
        ).model_dump(),
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
        ).model_dump(),
    )