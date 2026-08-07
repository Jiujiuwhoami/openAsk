"""会话管理 API 路由。

端点：
  - GET    /api/projects/{id}/conversations        会话列表（分页）
  - GET    /api/projects/{id}/conversations/{cid}  会话详情（含消息）
  - DELETE /api/projects/{id}/conversations/{cid}  删除会话
  - PUT    /api/projects/{id}/conversations/{cid}  更新会话标题
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.dependencies import get_current_user
from src.domain.user import User
from src.services.conversation_service import ConversationService
from src.services.project_service import ProjectService

router = APIRouter()
_conv_service = ConversationService()
_project_service = ProjectService()


def _verify_project_owner(project_id: str, user: User):
    """验证用户是项目所有者。"""
    project = _project_service.get_by_id(project_id)
    if not project or project.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")


class UpdateConversationTitleRequest(BaseModel):
    title: str = Field(..., description="会话标题", max_length=200)


@router.get("/api/projects/{project_id}/conversations")
async def list_conversations(
    project_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """获取项目会话列表（分页，按更新时间倒序）。"""
    _verify_project_owner(project_id, current_user)
    return _conv_service.list_conversations(project_id, page=page, page_size=page_size)


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