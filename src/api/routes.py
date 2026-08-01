"""API 路由定义。

多租户改造：
  - verify_api_key → resolve_tenant：从 X-API-Key 解析 Tenant 注入 request.state.tenant
  - 所有业务路由加 Depends(resolve_tenant)
  - /api/health 保留免鉴权
  - 新增 /api/admin/tenants 管理路由组（需要 admin 权限，暂用 admin_router 标记）
"""

import json
import os
import time
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request

from fastapi.responses import StreamingResponse

from src.utils.limiter import limiter
from src.api.schemas import (
    ChatRequest,
    ChatResponse,
    Source,
    DocumentRequest,
    UpdateDocumentRequest,
    DocumentResponse,
    SearchRequest,
    BatchSearchRequest,
    BatchSearchResultItem,
    SearchResultResponse,
    PaginatedResponse,
    HealthResponse,
    DeleteResponse,
    # 租户管理
    CreateTenantRequest,
    UpdateTenantRequest,
    TenantResponse,
    TenantKeyResponse,
    TenantStatsResponse,
)
from src.core.retriever import RetrievalResult
from src.domain.models import Tenant
from src.domain.exceptions import KnowledgeBaseError, DocumentNotFoundError, TenantNotFoundError
from src.services.tenant_service import TenantService
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api")
admin_router = APIRouter(prefix="/api/admin")

# ------------------------------------------------------------------
# 租户鉴权
# ------------------------------------------------------------------

_tenant_service: TenantService = None  # 在 lifespan 中初始化


def _get_tenant_service() -> TenantService:
    """获取全局 TenantService 实例（单例延迟初始化）。"""
    global _tenant_service
    if _tenant_service is None:
        _tenant_service = TenantService()
    return _tenant_service


def _verify_admin_key(request: Request) -> None:
    """管理端点鉴权：使用 API 全局 admin key。

    当前设计：管理端点使用 .env 中配置的 API_KEY（admin 级别）。
    未来可扩展为角色系统。
    """
    admin_key = settings.api.api_key
    if not admin_key:
        raise HTTPException(
            status_code=503,
            detail="管理员未配置 API_KEY，请在 .env 中配置 API_KEY",
        )
    provided = request.headers.get("X-API-Key")
    if provided != admin_key:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")


async def resolve_tenant(request: Request) -> Tenant:
    """FastAPI Depends：从 X-API-Key 解析租户，注入 request.state。

    两种鉴权方式：
    1. 租户 API Key：匹配某个活跃租户的 key → 返回该 Tenant
    2. 管理 API Key（API_API_KEY）：匹配时返回 default 租户

    将 Tenant 写入 request.state.tenant，下游可透传。
    """
    provided_key = request.headers.get("X-API-Key")

    # 优先：按租户 API Key 查找
    svc = _get_tenant_service()
    tenant = svc.get_by_api_key(provided_key)
    if tenant:
        request.state.tenant = tenant
        return tenant

    # 降级：使用管理 API Key 时返回 default 租户
    admin_key = settings.api.api_key
    if admin_key and provided_key == admin_key:
        default = svc.get_by_id("default") or svc.ensure_default_tenant()
        request.state.tenant = default
        return default

    raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")


async def resolve_optional_tenant(request: Request) -> Tenant:
    """可选的租户解析：无 key 时不报错，返回 default。

    用于健康检查等公开端点（但仍需要租户上下文）。
    """
    tenant = _get_tenant_service().get_by_id("default")
    if tenant:
        return tenant
    svc = _get_tenant_service()
    return svc.ensure_default_tenant()


# ------------------------------------------------------------------
# 健康检查（免鉴权）
# ------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request):
    """健康检查端点：探测所有关键依赖状态。"""
    zvec_status = "healthy"
    embedding_status = "healthy"
    llm_status = "healthy"
    cache_status = "healthy"
    document_count = 0
    overall_status = "healthy"

    try:
        vector_store = request.app.state.vector_store
        if hasattr(vector_store, "acount"):
            document_count = await vector_store.acount()
        else:
            document_count = vector_store.count()
    except Exception as e:
        zvec_status = f"unhealthy: {str(e)[:50]}"
        overall_status = "degraded"

    try:
        embedding_service = request.app.state.embedding_service
        dim = embedding_service.dimension()
        if dim == 0:
            embedding_status = "unhealthy: dimension is 0"
            overall_status = "degraded"
    except Exception as e:
        embedding_status = f"unhealthy: {str(e)[:50]}"
        overall_status = "degraded"

    try:
        factory = request.app.state.retriever_factory
        retriever = factory.get_retriever_for_tenant("default")
        if not retriever._llm_client.is_configured:
            llm_status = "warning: API key not configured"
    except Exception as e:
        llm_status = f"unhealthy: {str(e)[:50]}"

    try:
        factory = request.app.state.retriever_factory
        factory.get_retriever_for_tenant("default")
    except Exception as e:
        cache_status = f"unhealthy: {str(e)[:50]}"
        overall_status = "degraded"

    return HealthResponse(
        status=overall_status,
        version="1.0.0",
        timestamp=datetime.now(),
        zvec_status=zvec_status,
        embedding_status=embedding_status,
        llm_status=llm_status,
        cache_status=cache_status,
        document_count=document_count,
    )


# ------------------------------------------------------------------
# 服务工厂
# ------------------------------------------------------------------

async def get_retriever_for_tenant(request: Request) -> "Retriever":
    """按租户获取隔离的 Retriever 实例（通过 RetrieverFactory）。

    租户上下文由 resolve_tenant 注入到 request.state.tenant，
    本函数从中读取 tenant_id 和 tenant 对象。

    每个租户获得独立的 Retriever（含独立 LLM 响应缓存），
    共享 EmbeddingService / ZvecStore / Reranker。
    """
    factory = getattr(request.app.state, "retriever_factory", None)
    if factory is None:
        raise RuntimeError("RetrieverFactory 未初始化")
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise RuntimeError("租户上下文未注入，请先调用 resolve_tenant")
    return factory.get_retriever_for_tenant(tenant.tenant_id, tenant)


async def get_knowledge_service(request: Request):
    """获取 KnowledgeService 实例（从 app.state）。"""
    return request.app.state.knowledge_service


# ------------------------------------------------------------------
# 问答接口
# ------------------------------------------------------------------

@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    body: ChatRequest,
    tenant=Depends(resolve_tenant),
    retriever=Depends(get_retriever_for_tenant),
):
    """聊天接口：基于知识库回答用户问题。"""
    try:
        result: RetrievalResult = await retriever.retrieve(
            query=body.query,
            top_k=body.top_k,
            system_prompt=tenant.system_prompt or None,
            tenant_id=tenant.tenant_id,
        )

        sources = [
            Source(
                doc_id=s.doc_id,
                title=s.title,
                content=s.content,
                score=round(s.score, 4),
            )
            for s in result.sources
        ]

        return ChatResponse(
            answer=result.answer,
            sources=sources,
            cache_hit=result.cache_hit,
            llm_used=result.llm_used,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    tenant=Depends(resolve_tenant),
    retriever=Depends(get_retriever_for_tenant),
):
    """流式聊天接口：逐事件返回回答（SSE 格式）。"""

    async def event_generator():
        try:
            async for event in retriever.retrieve_stream(
                query=body.query,
                top_k=body.top_k,
                system_prompt=tenant.system_prompt or None,
                tenant_id=tenant.tenant_id,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'data': str(e)}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'event': 'done', 'data': None}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------
# 知识库 CRUD
# ------------------------------------------------------------------

@router.post("/knowledge", response_model=DocumentResponse)
@limiter.limit("30/minute")
async def create_document(
    request: Request,
    body: DocumentRequest,
    knowledge_service=Depends(get_knowledge_service),
    tenant=Depends(resolve_tenant),
):
    """创建文档接口。"""
    try:
        doc = await knowledge_service.create_document_from_text(
            title=body.title,
            content=body.content,
            tags=body.tags,
            source=body.source,
            tenant_id=tenant.tenant_id,
        )
        return DocumentResponse(
            doc_id=doc.doc_id,
            title=doc.title,
            content=doc.content,
            tags=doc.tags,
            source=doc.source,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
    except KnowledgeBaseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


ALLOWED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx", ".html"}


@router.post("/knowledge/upload")
@limiter.limit("10/minute")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    knowledge_service=Depends(get_knowledge_service),
    tenant=Depends(resolve_tenant),
):
    """上传文档接口。"""
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}，支持的格式: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=ext,
        ) as f:
            f.write(await file.read())
            temp_path = f.name

        doc = await knowledge_service.load_and_store_document(
            file_path=temp_path,
            tenant_id=tenant.tenant_id,
        )

        return DocumentResponse(
            doc_id=doc.doc_id,
            title=doc.title,
            content=doc.content,
            tags=doc.tags,
            source=doc.source,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
    except KnowledgeBaseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


@router.get("/knowledge/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: str,
    knowledge_service=Depends(get_knowledge_service),
    tenant=Depends(resolve_tenant),
):
    """获取文档接口。"""
    try:
        doc = await knowledge_service.get_by_id(doc_id, tenant_id=tenant.tenant_id)
        if not doc:
            raise DocumentNotFoundError(f"文档不存在: {doc_id}")
        return DocumentResponse(
            doc_id=doc.doc_id,
            title=doc.title,
            content=doc.content,
            tags=doc.tags,
            source=doc.source,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
    except DocumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/knowledge/{doc_id}", response_model=DocumentResponse)
@limiter.limit("30/minute")
async def update_document(
    request: Request,
    doc_id: str,
    body: UpdateDocumentRequest,
    knowledge_service=Depends(get_knowledge_service),
    tenant=Depends(resolve_tenant),
):
    """更新文档接口。"""
    try:
        doc = await knowledge_service.update_document(
            doc_id=doc_id,
            title=body.title,
            content=body.content,
            tags=body.tags,
            source=body.source,
            tenant_id=tenant.tenant_id,
        )
        return DocumentResponse(
            doc_id=doc.doc_id,
            title=doc.title,
            content=doc.content,
            tags=doc.tags,
            source=doc.source,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
    except DocumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except KnowledgeBaseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/knowledge/{doc_id}", response_model=DeleteResponse)
async def delete_document(
    doc_id: str,
    knowledge_service=Depends(get_knowledge_service),
    tenant=Depends(resolve_tenant),
):
    """删除文档接口。"""
    try:
        success = await knowledge_service.delete_document(doc_id, tenant_id=tenant.tenant_id)
        if success:
            return DeleteResponse(success=True, message="删除成功")
        else:
            return DeleteResponse(success=False, message="删除失败，文档可能不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# 搜索接口
# ------------------------------------------------------------------

@router.post("/search", response_model=list[SearchResultResponse])
@limiter.limit(settings.rate_limit.per_user)
async def search(
    request: Request,
    body: SearchRequest,
    knowledge_service=Depends(get_knowledge_service),
    tenant=Depends(resolve_tenant),
):
    """搜索接口。"""
    try:
        results = await knowledge_service.search(
            query=body.query,
            top_k=body.top_k,
            tenant_id=tenant.tenant_id,
        )
        return [
            SearchResultResponse(
                doc_id=r.doc_id,
                title=r.title,
                content=r.content[:500] + "..." if len(r.content) > 500 else r.content,
                score=round(r.score, 4) if hasattr(r, "score") else 0.0,
            )
            for r in results
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search/batch", response_model=list[BatchSearchResultItem])
@limiter.limit("30/minute")
async def batch_search(
    request: Request,
    body: BatchSearchRequest,
    knowledge_service=Depends(get_knowledge_service),
    tenant=Depends(resolve_tenant),
):
    """批量搜索接口。"""
    try:
        batch_results = await knowledge_service.batch_search(
            queries=body.queries,
            top_k=body.top_k,
            tenant_id=tenant.tenant_id,
        )
        items = []
        for i, (query, results) in enumerate(zip(body.queries, batch_results)):
            items.append(
                BatchSearchResultItem(
                    query_index=i,
                    query=query,
                    results=[
                        SearchResultResponse(
                            doc_id=r.doc_id,
                            title=r.title,
                            content=r.content[:500] + "..." if len(r.content) > 500 else r.content,
                            score=round(r.score, 4) if hasattr(r, "score") else 0.0,
                        )
                        for r in results
                    ],
                )
            )
        return items
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge", response_model=PaginatedResponse)
async def list_documents(
    page: int = 1,
    page_size: int = 10,
    knowledge_service=Depends(get_knowledge_service),
    tenant=Depends(resolve_tenant),
):
    """列出文档接口（分页）。"""
    try:
        docs = await knowledge_service.list_documents(
            page=page, page_size=page_size, tenant_id=tenant.tenant_id
        )
        total = await knowledge_service.count_documents(tenant_id=tenant.tenant_id)
        items = [
            DocumentResponse(
                doc_id=d.doc_id,
                title=d.title,
                content=d.content[:500] + "..." if len(d.content) > 500 else d.content,
                tags=d.tags,
                source=d.source,
                created_at=d.created_at,
                updated_at=d.updated_at,
            )
            for d in docs
        ]
        return PaginatedResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# /api/admin/tenants 管理路由组
# ------------------------------------------------------------------

@admin_router.get("/tenants", response_model=list[TenantResponse])
@limiter.limit("5/minute")
async def list_tenants(request: Request, include_deleted: bool = False):
    """租户列表（管理端）。

    Args:
        include_deleted: 是否包含已删除的租户（默认 False）。
    """
    _verify_admin_key(request)
    svc = _get_tenant_service()
    tenants = svc.list_tenants(include_deleted=include_deleted)
    return [
        TenantResponse(
            tenant_id=t.tenant_id,
            api_key=t.api_key,
            name=t.name,
            status=t.status,
            llm_api_base=t.llm_api_base,
            llm_model=t.llm_model,
            llm_timeout=t.llm_timeout,
            rate_limit_per_user=t.rate_limit_per_user,
            rate_limit_global=t.rate_limit_global,
            system_prompt=t.system_prompt,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in tenants
    ]


@admin_router.post("/tenants", response_model=TenantResponse)
@limiter.limit("5/minute")
async def create_tenant(request: Request, body: CreateTenantRequest):
    """创建租户（管理端）。"""
    _verify_admin_key(request)
    svc = _get_tenant_service()
    tenant = svc.create_tenant(
        name=body.name,
        status=body.status,
        knowledge_path=body.knowledge_path,
        llm_api_key=body.llm_api_key,
        llm_api_base=body.llm_api_base,
        llm_model=body.llm_model,
        llm_timeout=body.llm_timeout,
        rate_limit_per_user=body.rate_limit_per_user,
        rate_limit_global=body.rate_limit_global,
        system_prompt=body.system_prompt,
    )
    return TenantResponse(
        tenant_id=tenant.tenant_id,
        api_key=tenant.api_key,
        name=tenant.name,
        status=tenant.status,
        llm_api_base=tenant.llm_api_base,
        llm_model=tenant.llm_model,
        llm_timeout=tenant.llm_timeout,
        rate_limit_per_user=tenant.rate_limit_per_user,
        rate_limit_global=tenant.rate_limit_global,
        system_prompt=tenant.system_prompt,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )


@admin_router.get("/tenants/{tenant_id}", response_model=TenantResponse)
@limiter.limit("5/minute")
async def get_tenant(request: Request, tenant_id: str):
    """获取租户详情（管理端）。"""
    _verify_admin_key(request)
    svc = _get_tenant_service()
    t = svc.get_by_id(tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"租户不存在: {tenant_id}")
    return TenantResponse(
        tenant_id=t.tenant_id,
        api_key=t.api_key,
        name=t.name,
        status=t.status,
        llm_api_base=t.llm_api_base,
        llm_model=t.llm_model,
        llm_timeout=t.llm_timeout,
        rate_limit_per_user=t.rate_limit_per_user,
        rate_limit_global=t.rate_limit_global,
        system_prompt=t.system_prompt,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@admin_router.put("/tenants/{tenant_id}", response_model=TenantResponse)
@limiter.limit("5/minute")
async def update_tenant(request: Request, tenant_id: str, body: UpdateTenantRequest):
    """更新租户（管理端）。"""
    _verify_admin_key(request)
    svc = _get_tenant_service()
    try:
        t = svc.update_tenant(
            tenant_id=tenant_id,
            name=body.name or "",
            status=body.status or "",
            knowledge_path=body.knowledge_path or "",
            llm_api_key=body.llm_api_key or "",
            llm_api_base=body.llm_api_base or "",
            llm_model=body.llm_model or "",
            llm_timeout=body.llm_timeout or 0,
            rate_limit_per_user=body.rate_limit_per_user or "",
            rate_limit_global=body.rate_limit_global or "",
            system_prompt=body.system_prompt,
        )
    except TenantNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return TenantResponse(
        tenant_id=t.tenant_id,
        api_key=t.api_key,
        name=t.name,
        status=t.status,
        llm_api_base=t.llm_api_base,
        llm_model=t.llm_model,
        llm_timeout=t.llm_timeout,
        rate_limit_per_user=t.rate_limit_per_user,
        rate_limit_global=t.rate_limit_global,
        system_prompt=t.system_prompt,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@admin_router.delete("/tenants/{tenant_id}", response_model=DeleteResponse)
@limiter.limit("5/minute")
async def delete_tenant_endpoint(request: Request, tenant_id: str):
    """删除租户（管理端，软删除）。

    删除流程：
      1. 软删除租户记录（标记 status=deleted）
      2. 同步删除该租户在 Zvec 中的所有知识库文档
      3. 返回被删除的文档数
    """
    _verify_admin_key(request)
    svc = _get_tenant_service()

    # 确认租户存在
    tenant = svc.get_by_id(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"租户不存在: {tenant_id}")

    # 同步删除该租户的知识库文档
    vector_store = getattr(request.app.state, "vector_store", None)
    deleted_doc_count = 0
    if vector_store and hasattr(vector_store, "adelete_by_tenant_id"):
        try:
            deleted_doc_count = await vector_store.adelete_by_tenant_id(tenant_id)
        except Exception as e:
            logger.warning(f"删除租户文档失败，但不阻断租户删除: {e}")

    success = svc.delete_tenant(tenant_id)
    if success:
        message = f"删除成功"
        if deleted_doc_count > 0:
            message += f"（同步删除 {deleted_doc_count} 篇文档）"
        return DeleteResponse(success=True, message=message)
    raise HTTPException(status_code=404, detail=f"租户不存在: {tenant_id}")


@admin_router.post("/tenants/{tenant_id}/rotate-key", response_model=TenantKeyResponse)
@limiter.limit("5/minute")
async def rotate_api_key(request: Request, tenant_id: str):
    """轮换租户 API Key（管理端）。"""
    _verify_admin_key(request)
    svc = _get_tenant_service()
    try:
        new_key = svc.rotate_api_key(tenant_id)
    except TenantNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return TenantKeyResponse(api_key=new_key)


@admin_router.get("/tenants/{tenant_id}/stats", response_model=TenantStatsResponse)
@limiter.limit("5/minute")
async def get_tenant_stats(request: Request, tenant_id: str):
    """获取租户统计（管理端）。"""
    _verify_admin_key(request)
    svc = _get_tenant_service()
    t = svc.get_by_id(tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"租户不存在: {tenant_id}")

    # 从 Zvec 查询文档数
    vector_store = request.app.state.vector_store if hasattr(request.app.state, "vector_store") else None
    doc_count = 0
    if vector_store and hasattr(vector_store, "count"):
        try:
            doc_count = vector_store.count(tenant_id=tenant_id)
        except Exception:
            pass

    # 从统计注册表读取真实调用数据
    stats_registry = getattr(request.app.state, "stats_registry", None)
    if stats_registry is not None:
        stats = stats_registry.get_stats(tenant_id)
        if stats is not None:
            return TenantStatsResponse(
                tenant_id=tenant_id,
                document_count=doc_count,
                total_calls=stats.total_calls,
                prompt_tokens=stats.prompt_tokens,
                completion_tokens=stats.completion_tokens,
                cache_hit_rate=stats.cache_hit_rate,
                created_at=t.created_at,
                last_request=int(stats.last_call_at),
            )

    return TenantStatsResponse(
        tenant_id=tenant_id,
        document_count=doc_count,
        total_calls=0,
        prompt_tokens=0,
        completion_tokens=0,
        cache_hit_rate=0.0,
        created_at=t.created_at,
        last_request=0,
    )
