"""API 路由定义。

业务路由（chat, knowledge, search）使用 X-API-Key 鉴权，
通过 ProjectService 解析为 Project 实例。

多轮对话：前端传 conversation_id，后端从 ConversationService 恢复历史并注入 LLM。
"""

import json
import os
import time
import tempfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Request

from fastapi.responses import StreamingResponse

from src.utils.limiter import limiter
from src.services.analytics_service import AnalyticsService
from src.services.plan_service import PlanService
from src.services.sensitive_filter import SensitiveFilterService
from src.services.conversation_service import ConversationService
from src.api.schemas import (
    ChatRequest,
    ChatResponse,
    ChatMessage,
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
    BatchDeleteRequest,
    WidgetMessageRequest,
    PollResponse,
)
from src.core.retriever import RetrievalResult
from src.domain.project import Project
from src.domain.exceptions import KnowledgeBaseError, DocumentNotFoundError
from src.services.project_service import ProjectService
from src.utils.config import settings
from src.utils.logger import get_logger
from src.api.dependencies import resolve_widget_project

logger = get_logger(__name__)

router = APIRouter(prefix="/api")

# 全局服务实例
_project_service = ProjectService()
_conv_service = ConversationService()


async def resolve_project(request: Request) -> Project:
    """从 X-API-Key 解析当前 Project。

    用于 chat, knowledge, search 等业务路由的鉴权。
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="缺少 X-API-Key")

    project = _project_service.get_by_api_key(api_key)
    if project is None:
        raise HTTPException(status_code=401, detail="无效的 API Key")
    if not project.is_active:
        raise HTTPException(status_code=403, detail="项目已被禁用")

    request.state.project = project
    return project


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
        retriever = factory.get_retriever_for_project("default")
        if not retriever._llm_client.is_configured:
            llm_status = "warning: API key not configured"
    except Exception as e:
        llm_status = f"unhealthy: {str(e)[:50]}"

    try:
        factory = request.app.state.retriever_factory
        factory.get_retriever_for_project("default")
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

async def get_retriever_for_project(request: Request) -> "Retriever":
    """按项目获取隔离的 Retriever 实例（通过 RetrieverFactory）。"""
    factory = getattr(request.app.state, "retriever_factory", None)
    if factory is None:
        raise RuntimeError("RetrieverFactory 未初始化")
    project = getattr(request.state, "project", None)
    if project is None:
        raise RuntimeError("项目上下文未注入")
    return factory.get_retriever_for_project(project.project_id, project)


async def get_knowledge_service(request: Request):
    """获取 KnowledgeService 实例（从 app.state）。"""
    return request.app.state.knowledge_service


# ------------------------------------------------------------------
# 问答接口
# ------------------------------------------------------------------

def _build_conversation_title(query: str) -> str:
    """从首条查询截取会话标题。"""
    return query[:100] if len(query) > 100 else query


def _get_messages_for_llm(
    query: str,
    conversation_id: Optional[str] = None,
    request_messages: Optional[list] = None,
) -> tuple:
    """获取注入 LLM 的历史消息列表。

    优先级：request_messages > conversation_id 历史 > 无历史

    Returns:
        (llm_messages: List[dict] | None, conv_id: str)
    """
    if request_messages:
        # 使用前端传来的消息（前端直接管理历史）
        return [{"role": m.role, "content": m.content} for m in request_messages], conversation_id or ""

    if conversation_id:
        # 从服务端恢复历史
        history = _conv_service.get_history_as_messages(conversation_id, limit=10)
        conv = _conv_service.get_conversation(conversation_id)
        if conv:
            return history, conversation_id

    return None, ""


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    body: ChatRequest,
    project=Depends(resolve_widget_project),
    retriever=Depends(get_retriever_for_project),
):
    """聊天接口：基于知识库回答用户问题。

    支持多轮对话：
    - 新对话：不传 conversation_id，后端自动创建
    - 续传：传 conversation_id，后端恢复历史
    - 也可传 messages 数组自行管理历史
    """
    # 敏感词检查
    _sensitive = SensitiveFilterService()
    if _sensitive.contains_sensitive(body.query, project.project_id):
        raise HTTPException(status_code=400, detail="提问包含敏感词，请重新输入")

    # 确定语言（请求级覆盖 > 项目设置）
    language = body.language or project.language

    # 会话管理
    conversation_id = body.conversation_id or ""
    conv = None
    if not conversation_id:
        # 新对话：创建会话
        title = _build_conversation_title(body.query)
        conv = _conv_service.create_conversation(project.project_id, title=title)
        conversation_id = conv.conversation_id
    else:
        conv = _conv_service.get_conversation(conversation_id)
        if not conv or conv.project_id != project.project_id:
            raise HTTPException(status_code=404, detail="会话不存在")

    # 人工接管守卫：会话处于 agent 模式时不走 AI 回答
    if conv and conv.status == "agent":
        raise HTTPException(status_code=409, detail="会话已被人工客服接管")

    # 获取历史消息（用于 LLM 上下文）
    # 注意：先获取历史再追加用户消息，避免 LLM 收到重复的当前查询
    llm_messages, _ = _get_messages_for_llm(
        body.query, conversation_id, body.messages
    )

    # 追加用户消息
    _conv_service.add_message(conversation_id, "user", body.query)

    try:
        result: RetrievalResult = await retriever.retrieve(
            query=body.query,
            top_k=body.top_k,
            system_prompt=project.system_prompt or None,
            project_id=project.project_id,
            messages=llm_messages,
            language=language,
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

        # 追加助手消息
        _conv_service.add_message(conversation_id, "assistant", result.answer)

        # 自动更新标题（首条消息后）
        if conv and conv.message_count == 0:
            _conv_service.update_title(conversation_id, _build_conversation_title(body.query))

        # 记录日志
        try:
            AnalyticsService().record_chat(
                project_id=project.project_id,
                query=body.query,
                answer=result.answer,
                sources=[s.model_dump() for s in sources],
                cache_hit=result.cache_hit,
                llm_used=result.llm_used,
                conversation_id=conversation_id,
            )
        except Exception as log_err:
            logger.warning(f"记录问答日志失败: {log_err}")

        return ChatResponse(
            answer=result.answer,
            sources=sources,
            cache_hit=result.cache_hit,
            llm_used=result.llm_used,
            conversation_id=conversation_id,
            handoff_suggested=result.handoff_suggested,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    project=Depends(resolve_widget_project),
    retriever=Depends(get_retriever_for_project),
):
    """流式聊天接口：逐事件返回回答（SSE 格式）。

    支持多轮对话（同 /api/chat）。
    流式完成后自动记录日志到 chat_logs。
    """

    # 敏感词检查
    _sensitive = SensitiveFilterService()
    if _sensitive.contains_sensitive(body.query, project.project_id):
        async def error_gen():
            yield f"data: {json.dumps({'event': 'error', 'data': '提问包含敏感词，请重新输入'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'event': 'done', 'data': None}, ensure_ascii=False)}\n\n"
        return StreamingResponse(
            error_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    # 确定语言
    language = body.language or project.language

    # 会话管理
    conversation_id = body.conversation_id or ""
    conv = None
    if not conversation_id:
        title = _build_conversation_title(body.query)
        conv = _conv_service.create_conversation(project.project_id, title=title)
        conversation_id = conv.conversation_id
    else:
        conv = _conv_service.get_conversation(conversation_id)
        if not conv or conv.project_id != project.project_id:
            async def not_found_gen():
                yield f"data: {json.dumps({'event': 'error', 'data': '会话不存在'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'event': 'done', 'data': None}, ensure_ascii=False)}\n\n"
            return StreamingResponse(
                not_found_gen(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
            )

    # 人工接管守卫：会话处于 agent 模式时不走 AI 回答
    if conv and conv.status == "agent":
        async def agent_mode_gen():
            yield f"data: {json.dumps({'event': 'error', 'data': '会话已被人工客服接管'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'event': 'done', 'data': None}, ensure_ascii=False)}\n\n"
        return StreamingResponse(
            agent_mode_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    # 获取历史消息（先获取再追加，避免 LLM 收到重复的当前查询）
    llm_messages, _ = _get_messages_for_llm(
        body.query, conversation_id, body.messages
    )

    # 追加用户消息
    _conv_service.add_message(conversation_id, "user", body.query)

    async def event_generator():
        answer_chunks: list[str] = []
        sources_data: list = []
        cache_hit = False
        llm_used = False
        handoff_suggested = False

        # 先发送会话 ID（前端保存用于续传）
        yield f"data: {json.dumps({'event': 'conversation_id', 'data': conversation_id}, ensure_ascii=False)}\n\n"

        try:
            async for event in retriever.retrieve_stream(
                query=body.query,
                top_k=body.top_k,
                system_prompt=project.system_prompt or None,
                project_id=project.project_id,
                messages=llm_messages,
                language=language,
            ):
                # 收集数据用于日志
                if event["event"] == "sources":
                    sources_data = event["data"] or []
                elif event["event"] == "cache_hit":
                    cache_hit = bool(event["data"])
                elif event["event"] == "handoff_suggested":
                    handoff_suggested = bool(event["data"])
                elif event["event"] == "answer_delta":
                    answer_chunks.append(event["data"] or "")
                elif event["event"] == "done":
                    llm_used = True

                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'data': str(e)}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'event': 'done', 'data': None}, ensure_ascii=False)}\n\n"

        # 流式完成后记录日志和会话
        full_answer = "".join(answer_chunks)
        if full_answer:
            try:
                # 追加助手消息
                _conv_service.add_message(conversation_id, "assistant", full_answer)

                # 自动更新标题
                if conv and conv.message_count == 0:
                    _conv_service.update_title(conversation_id, _build_conversation_title(body.query))

                # 记录日志
                AnalyticsService().record_chat(
                    project_id=project.project_id,
                    query=body.query,
                    answer=full_answer,
                    sources=sources_data,
                    cache_hit=cache_hit,
                    llm_used=llm_used,
                    conversation_id=conversation_id,
                )
            except Exception as log_err:
                logger.warning(f"记录流式问答日志失败: {log_err}")

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
# 人工客服 — Widget 端轮询/消息（X-API-Key 鉴权）
# ------------------------------------------------------------------


@router.get("/chat/poll", response_model=PollResponse)
async def widget_poll(
    conversation_id: str = Query(..., description="会话 ID"),
    since_id: int = Query(0, ge=0, description="上次收到的最大消息 ID"),
    project=Depends(resolve_widget_project),
):
    """Widget 轮询：获取会话状态变更和客服新消息。"""
    conv = _conv_service.get_conversation(conversation_id)
    if not conv or conv.project_id != project.project_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    messages = _conv_service.get_messages_since(conversation_id, since_id=since_id)
    return PollResponse(
        status=conv.status,
        agent_id=conv.agent_id,
        messages=[
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "message_type": m.message_type,
                "created_at": m.created_at,
            }
            for m in messages
        ],
    )


@router.post("/chat/message")
async def widget_send_message(
    body: WidgetMessageRequest,
    project=Depends(resolve_widget_project),
):
    """Widget 用户发送消息（人工模式）。仅当会话处于 agent 状态时可用。"""
    conv = _conv_service.get_conversation(body.conversation_id)
    if not conv or conv.project_id != project.project_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.status != "agent":
        raise HTTPException(status_code=409, detail="会话未被人工接管，请使用 /api/chat")

    msg = _conv_service.add_message(body.conversation_id, "user", body.content)
    return {"id": msg.id, "role": "user", "content": msg.content, "created_at": msg.created_at}


# ------------------------------------------------------------------
# 知识库 CRUD
# ------------------------------------------------------------------

@router.post("/knowledge", response_model=DocumentResponse)
@limiter.limit("30/minute")
async def create_document(
    request: Request,
    body: DocumentRequest,
    knowledge_service=Depends(get_knowledge_service),
    project=Depends(resolve_project),
):
    """创建文档接口。"""
    _plan_svc = PlanService()
    _current_count = await knowledge_service.count_documents(project_id=project.project_id)
    _check = _plan_svc.check_limits(project.project_id, document_count=_current_count + 1)
    if not _check["allowed"]:
        raise HTTPException(status_code=402, detail=_check["reason"])

    try:
        doc = await knowledge_service.create_document_from_text(
            title=body.title,
            content=body.content,
            tags=body.tags,
            source=body.source,
            project_id=project.project_id,
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
    project=Depends(resolve_project),
):
    """上传文档接口。"""
    _plan_svc = PlanService()
    _current_count = await knowledge_service.count_documents(project_id=project.project_id)
    _check = _plan_svc.check_limits(project.project_id, document_count=_current_count + 1)
    if not _check["allowed"]:
        raise HTTPException(status_code=402, detail=_check["reason"])

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
            project_id=project.project_id,
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
    project=Depends(resolve_project),
):
    """获取文档接口。"""
    try:
        doc = await knowledge_service.get_by_id(doc_id, project_id=project.project_id)
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
    project=Depends(resolve_project),
):
    """更新文档接口。"""
    try:
        doc = await knowledge_service.update_document(
            doc_id=doc_id,
            title=body.title,
            content=body.content,
            tags=body.tags,
            source=body.source,
            project_id=project.project_id,
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
    project=Depends(resolve_project),
):
    """删除文档接口。"""
    try:
        success = await knowledge_service.delete_document(doc_id, project_id=project.project_id)
        if success:
            return DeleteResponse(success=True, message="删除成功")
        else:
            return DeleteResponse(success=False, message="删除失败，文档可能不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/knowledge/batch-delete", response_model=DeleteResponse)
@limiter.limit("10/minute")
async def batch_delete_documents(
    request: Request,
    body: BatchDeleteRequest,
    knowledge_service=Depends(get_knowledge_service),
    project=Depends(resolve_project),
):
    """批量删除文档接口。"""
    if not body.doc_ids:
        raise HTTPException(status_code=400, detail="文档 ID 列表不能为空")
    try:
        count = await knowledge_service.batch_delete_documents(
            doc_ids=body.doc_ids,
            project_id=project.project_id,
        )
        return DeleteResponse(
            success=count > 0,
            message=f"成功删除 {count}/{len(body.doc_ids)} 篇文档",
        )
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
    project=Depends(resolve_project),
):
    """搜索接口。"""
    try:
        results = await knowledge_service.search(
            query=body.query,
            top_k=body.top_k,
            project_id=project.project_id,
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
    project=Depends(resolve_project),
):
    """批量搜索接口。"""
    try:
        batch_results = await knowledge_service.batch_search(
            queries=body.queries,
            top_k=body.top_k,
            project_id=project.project_id,
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
    search: str = Query("", description="按标题/内容/标签搜索"),
    tag: str = Query("", description="按标签筛选"),
    knowledge_service=Depends(get_knowledge_service),
    project=Depends(resolve_project),
):
    """列出文档接口（分页），支持搜索和标签筛选。"""
    try:
        if search.strip() or tag.strip():
            docs, total = await knowledge_service.list_documents_filtered(
                page=page, page_size=page_size, project_id=project.project_id,
                search=search, tag=tag,
            )
        else:
            docs = await knowledge_service.list_documents(
                page=page, page_size=page_size, project_id=project.project_id
            )
            total = await knowledge_service.count_documents(project_id=project.project_id)
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