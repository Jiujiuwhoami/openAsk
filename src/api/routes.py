"""API 路由定义。"""

import os
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request

from src.utils.limiter import limiter
from src.api.schemas import (
    ChatRequest,
    ChatResponse,
    Source,
    DocumentRequest,
    DocumentResponse,
    SearchRequest,
    SearchResultResponse,
    PaginatedResponse,
    HealthResponse,
    DeleteResponse,
)
from src.core.retriever import RetrievalResult
from src.domain.exceptions import KnowledgeBaseError, DocumentNotFoundError
from src.utils.config import settings

router = APIRouter(prefix="/api")


async def verify_api_key(request: Request):
    """API Key 认证依赖：验证请求中的 X-API-Key 头。"""
    api_key = settings.api.api_key
    if not api_key:
        return
    provided_key = request.headers.get("X-API-Key")
    if provided_key != api_key:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")


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
        llm_client = request.app.state.llm_client
        if not llm_client.is_configured:
            llm_status = "warning: API key not configured"
    except Exception as e:
        llm_status = f"unhealthy: {str(e)[:50]}"

    try:
        cache_backend = request.app.state.cache_backend
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


async def get_retriever(request: Request):
    """获取 Retriever 实例（从 app.state）。"""
    return request.app.state.retriever


async def get_knowledge_service(request: Request):
    """获取 KnowledgeService 实例（从 app.state）。"""
    return request.app.state.knowledge_service


@router.post("/chat", response_model=ChatResponse)
@limiter.limit("60/minute")
async def chat(
    body: ChatRequest,
    retriever=Depends(get_retriever),
    _=Depends(verify_api_key),
):
    """聊天接口：基于知识库回答用户问题。"""
    try:
        result: RetrievalResult = await retriever.retrieve(
            query=body.query,
            top_k=body.top_k,
        )

        sources = [
            Source(
                doc_id=s.doc_id,
                title=s.title,
                content=s.content[:500] + "..." if len(s.content) > 500 else s.content,
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


@router.post("/knowledge", response_model=DocumentResponse)
@limiter.limit("30/minute")
async def create_document(
    body: DocumentRequest,
    knowledge_service=Depends(get_knowledge_service),
    _=Depends(verify_api_key),
):
    """创建文档接口。"""
    try:
        doc = await knowledge_service.create_document_from_text(
            title=body.title,
            content=body.content,
            tags=body.tags,
            source=body.source,
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
    file: UploadFile = File(...),
    knowledge_service=Depends(get_knowledge_service),
    _=Depends(verify_api_key),
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
    _=Depends(verify_api_key),
):
    """获取文档接口。"""
    try:
        doc = knowledge_service.get_by_id(doc_id)
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


@router.delete("/knowledge/{doc_id}", response_model=DeleteResponse)
async def delete_document(
    doc_id: str,
    knowledge_service=Depends(get_knowledge_service),
    _=Depends(verify_api_key),
):
    """删除文档接口。"""
    try:
        success = knowledge_service.delete_document(doc_id)
        if success:
            return DeleteResponse(success=True, message="删除成功")
        else:
            return DeleteResponse(success=False, message="删除失败，文档可能不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search", response_model=list[SearchResultResponse])
@limiter.limit("60/minute")
async def search(
    body: SearchRequest,
    knowledge_service=Depends(get_knowledge_service),
    _=Depends(verify_api_key),
):
    """搜索接口。"""
    try:
        results = await knowledge_service.search(
            query=body.query,
            top_k=body.top_k,
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


@router.get("/knowledge", response_model=PaginatedResponse)
async def list_documents(
    page: int = 1,
    page_size: int = 10,
    knowledge_service=Depends(get_knowledge_service),
    _=Depends(verify_api_key),
):
    """列出文档接口（分页）。"""
    try:
        docs = knowledge_service.list_documents(page=page, page_size=page_size)
        total = knowledge_service.count_documents()
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
