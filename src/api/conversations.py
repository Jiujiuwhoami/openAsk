"""会话管理 API 路由。

端点：
  - GET    /api/projects/{id}/conversations               会话列表（分页，可选按状态筛选）
  - GET    /api/projects/{id}/conversations/{cid}         会话详情（含消息）
  - DELETE /api/projects/{id}/conversations/{cid}         删除会话
  - PUT    /api/projects/{id}/conversations/{cid}         更新会话标题
  - POST   /api/projects/{id}/conversations/{cid}/takeover   客服接管对话
  - POST   /api/projects/{id}/conversations/{cid}/release    释放回 AI 模式
  - POST   /api/projects/{id}/conversations/{cid}/messages   客服发送消息
  - GET    /api/projects/{id}/conversations/{cid}/poll       轮询新消息
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.dependencies import get_current_user
from src.api.schemas import AgentMessageRequest, PollResponse
from src.domain.user import User
from src.services.analytics_service import AnalyticsService
from src.services.conversation_service import ConversationService
from src.services.project_service import ProjectService
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()
_conv_service = ConversationService()
_project_service = ProjectService()
_analytics = AnalyticsService()


async def _notify_status(conversation_id: str, status: str, agent_id: str = ""):
    """推送会话状态变更到 WebSocket 订阅者。"""
    try:
        from src.api.ws import notify_conversation_status
        await notify_conversation_status(conversation_id, status, agent_id)
    except Exception as e:
        logger.warning(f"WebSocket 状态通知失败: {e}")


def _verify_project_owner(project_id: str, user: User):
    """验证用户是项目所有者。"""
    project = _project_service.get_by_id(project_id)
    if not project or project.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")


class UpdateConversationTitleRequest(BaseModel):
    title: str = Field(..., description="会话标题", max_length=200)


class TransferRequest(BaseModel):
    target_agent_id: str = Field(..., description="目标客服 ID")
    reason: str = Field("", description="转接原因", max_length=500)


class TagRequest(BaseModel):
    tag: str = Field(..., description="标签", min_length=1, max_length=50)


@router.get("/api/projects/{project_id}/conversations")
async def list_conversations(
    project_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, pattern="^(active|queuing|agent)$"),
    current_user: User = Depends(get_current_user),
):
    """获取项目会话列表（分页，按更新时间倒序，可选按状态筛选）。"""
    _verify_project_owner(project_id, current_user)
    return _conv_service.list_conversations(
        project_id, page=page, page_size=page_size, status=status or ""
    )


@router.get("/api/projects/{project_id}/conversations/{conversation_id}")
async def get_conversation(
    project_id: str,
    conversation_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
):
    """获取会话详情（含消息列表）。"""
    _verify_project_owner(project_id, current_user)
    conv = _conv_service.get_conversation(conversation_id)
    if not conv or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = _conv_service.get_messages_by_conversation(
        conversation_id, page=page, page_size=page_size
    )
    return {
        "conversation_id": conv.conversation_id,
        "project_id": conv.project_id,
        "title": conv.title,
        "status": conv.status,
        "agent_id": conv.agent_id,
        "message_count": conv.message_count,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "messages": messages["items"],
        "total_messages": messages["total"],
    }


@router.delete("/api/projects/{project_id}/conversations/{conversation_id}")
async def delete_conversation(
    project_id: str,
    conversation_id: str,
    current_user: User = Depends(get_current_user),
):
    """删除会话（物理删除，含所有消息）。"""
    _verify_project_owner(project_id, current_user)
    conv = _conv_service.get_conversation(conversation_id)
    if not conv or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    _conv_service.delete_conversation(conversation_id)
    return {"success": True, "message": "会话已删除"}


@router.put("/api/projects/{project_id}/conversations/{conversation_id}")
async def update_conversation_title(
    project_id: str,
    conversation_id: str,
    body: UpdateConversationTitleRequest,
    current_user: User = Depends(get_current_user),
):
    """更新会话标题。"""
    _verify_project_owner(project_id, current_user)
    conv = _conv_service.get_conversation(conversation_id)
    if not conv or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    _conv_service.update_title(conversation_id, body.title)
    return {"success": True, "message": "标题已更新"}


# ================================================================
# 人工客服接管
# ================================================================


@router.post("/api/projects/{project_id}/conversations/{conversation_id}/takeover")
async def takeover_conversation(
    project_id: str,
    conversation_id: str,
    current_user: User = Depends(get_current_user),
):
    """客服接管对话。状态 → agent，自动解决该对话的待处理转接请求。"""
    _verify_project_owner(project_id, current_user)
    conv = _conv_service.get_conversation(conversation_id)
    if not conv or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.status == "agent":
        raise HTTPException(status_code=409, detail="会话已被接管")

    _conv_service.update_status(conversation_id, "agent", current_user.user_id)
    # 自动解决该对话的转接请求
    _analytics.resolve_handoffs_by_conversation(project_id, conversation_id)
    # 推送状态变更通知
    await _notify_status(conversation_id, "agent", current_user.user_id)
    return {"success": True, "status": "agent", "agent_id": current_user.user_id}


@router.post("/api/projects/{project_id}/conversations/{conversation_id}/release")
async def release_conversation(
    project_id: str,
    conversation_id: str,
    current_user: User = Depends(get_current_user),
):
    """释放会话回 AI 模式。"""
    _verify_project_owner(project_id, current_user)
    conv = _conv_service.get_conversation(conversation_id)
    if not conv or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.status != "agent":
        raise HTTPException(status_code=409, detail="会话不在接管状态")

    _conv_service.update_status(conversation_id, "active", "")
    # 推送状态变更通知
    await _notify_status(conversation_id, "active", "")
    return {"success": True, "status": "active"}


@router.post("/api/projects/{project_id}/conversations/{conversation_id}/transfer")
async def transfer_conversation(
    project_id: str,
    conversation_id: str,
    body: TransferRequest,
    current_user: User = Depends(get_current_user),
):
    """将会话转接给其他客服。

    当前客服释放会话，目标客服接管。
    会话历史完整保留，添加转接通知消息。
    """
    _verify_project_owner(project_id, current_user)
    conv = _conv_service.get_conversation(conversation_id)
    if not conv or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.status != "agent":
        raise HTTPException(status_code=409, detail="会话未被接管，无法转接")

    old_agent_id = conv.agent_id
    target_agent_id = body.target_agent_id
    if target_agent_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="不能转接给自己")

    # 更新会话 agent_id
    _conv_service.update_status(conversation_id, "agent", target_agent_id)

    # 添加系统通知消息
    _conv_service.add_message(
        conversation_id,
        "system",
        f"会话已由 {current_user.user_id[:8]} 转接给 {target_agent_id[:8]}",
        metadata=f'{{"transfer_from":"{current_user.user_id}","transfer_to":"{target_agent_id}","reason":"{body.reason or ""}"}}',
    )

    # 通知旧客服（通过 WS 广播项目）
    try:
        from src.api.ws import notify_conversation_status
        await notify_conversation_status(conversation_id, "agent", target_agent_id)
    except Exception as e:
        logger.warning(f"转接 WS 通知失败: {e}")

    return {
        "success": True,
        "message": "会话已转接",
        "from_agent": current_user.user_id,
        "to_agent": target_agent_id,
    }


@router.get("/api/projects/{project_id}/conversations/{conversation_id}/tags")
async def get_conversation_tags(
    project_id: str,
    conversation_id: str,
    current_user: User = Depends(get_current_user),
):
    """获取会话标签。"""
    _verify_project_owner(project_id, current_user)
    conv = _conv_service.get_conversation(conversation_id)
    if not conv or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"tags": _conv_service.get_tags(conversation_id)}


@router.post("/api/projects/{project_id}/conversations/{conversation_id}/tags")
async def add_conversation_tag(
    project_id: str,
    conversation_id: str,
    body: TagRequest,
    current_user: User = Depends(get_current_user),
):
    """添加会话标签。"""
    _verify_project_owner(project_id, current_user)
    conv = _conv_service.get_conversation(conversation_id)
    if not conv or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    tags = _conv_service.add_tag(conversation_id, body.tag)
    return {"success": True, "tags": tags}


@router.delete("/api/projects/{project_id}/conversations/{conversation_id}/tags")
async def remove_conversation_tag(
    project_id: str,
    conversation_id: str,
    tag: str = Query(..., description="标签"),
    current_user: User = Depends(get_current_user),
):
    """移除会话标签。"""
    _verify_project_owner(project_id, current_user)
    conv = _conv_service.get_conversation(conversation_id)
    if not conv or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    tags = _conv_service.remove_tag(conversation_id, tag)
    return {"success": True, "tags": tags}


@router.get("/api/projects/{project_id}/conversations/tags/overview")
async def list_project_tags(
    project_id: str,
    current_user: User = Depends(get_current_user),
):
    """获取项目所有标签及使用次数。"""
    _verify_project_owner(project_id, current_user)
    return _conv_service.list_project_tags(project_id)


@router.post("/api/projects/{project_id}/conversations/{conversation_id}/messages")
async def send_agent_message(
    project_id: str,
    conversation_id: str,
    body: AgentMessageRequest,
    current_user: User = Depends(get_current_user),
):
    """客服发送消息（仅限已接管状态）。"""
    _verify_project_owner(project_id, current_user)
    conv = _conv_service.get_conversation(conversation_id)
    if not conv or conv.project_id != project_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.status != "agent":
        raise HTTPException(status_code=409, detail="会话未被接管，无法发送客服消息")

    msg = _conv_service.add_message(
        conversation_id,
        "agent",
        body.content,
        metadata=f'{{"agent_id":"{current_user.user_id}","agent_name":"{current_user.name or current_user.email}"}}',
    )
    return {
        "id": msg.id,
        "role": "agent",
        "content": msg.content,
        "message_type": msg.message_type,
        "created_at": msg.created_at,
    }


@router.get("/api/projects/{project_id}/conversations/{conversation_id}/poll", response_model=PollResponse)
async def poll_conversation(
    project_id: str,
    conversation_id: str,
    since_id: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
):
    """轮询对话新消息及状态变更。"""
    _verify_project_owner(project_id, current_user)
    conv = _conv_service.get_conversation(conversation_id)
    if not conv or conv.project_id != project_id:
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