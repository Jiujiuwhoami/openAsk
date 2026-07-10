"""API 路由定义。"""

import os
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request

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

router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查端点。"""
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        timestamp=datetime.now(),
    )


async def get_retriever(request: Request):
    """获取 Retriever 实例（从 app.state）。"""
    return request.app.state.retriever


async def get_knowledge_service(request: Request):
    """获取 KnowledgeService 实例（从 app.state）。"""
    return request.app.state.knowledge_service


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    retriever=Depends(get_retriever),
):
    """聊天接口：基于知识库回答用户问题。"""
    try:
        result: RetrievalResult = await retriever.retrieve(
            query=request.query,
            top_k=request.top_k,
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
async def create_document(
    request: DocumentRequest,
    knowledge_service=Depends(get_knowledge_service),
):
    """创建文档接口。"""
    try:
        doc = await knowledge_service.create_document_from_text(
            title=request.title,
            content=request.content,
            tags=request.tags,
            source=request.source,
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


@router.post("/knowledge/upload")
async def upload_document(
    file: UploadFile = File(...),
    knowledge_service=Depends(get_knowledge_service),
):
    """上传文档接口。"""
    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=os.path.splitext(file.filename)[1],
        ) as f:
            f.write(await file.read())
            temp_path = f.name

        doc = await knowledge_service.load_and_store_document(
            file_path=temp_path,
        )

        os.unlink(temp_path)

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


@router.get("/knowledge/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: str,
    knowledge_service=Depends(get_knowledge_service),
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
async def search(
    request: SearchRequest,
    knowledge_service=Depends(get_knowledge_service),
):
    """搜索接口。"""
    try:
        results = await knowledge_service.search(
            query=request.query,
            top_k=request.top_k,
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
