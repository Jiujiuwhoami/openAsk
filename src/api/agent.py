"""客服管理 API 路由。

端点：
  - PUT    /api/agent/status                   更新当前客服状态
  - GET    /api/projects/{id}/agents           获取项目客服列表
  - GET    /api/projects/{id}/agents/online    获取在线客服列表
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.dependencies import get_current_user
from src.domain.user import User
from src.services.agent_service import AgentService
from src.services.project_service import ProjectService
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()
_agent_service = AgentService()
_project_service = ProjectService()


class UpdateStatusRequest(BaseModel):
    status: str = Field(..., pattern="^(online|busy|away|offline)$")
    max_load: int = Field(5, ge=1, le=20)
    auto_accept: bool = Field(True)
    skills: Optional[List[str]] = Field(None, description="客服技能标签列表")


@router.put("/api/agent/status")
async def update_agent_status(
    body: UpdateStatusRequest,
    current_user: User = Depends(get_current_user),
):
    """更新当前客服的在线状态。"""
    projects = _project_service.list_by_user(current_user.user_id)
    if not projects:
        raise HTTPException(status_code=404, detail="未找到项目")

    # 更新所有项目中的状态（客服可能属于多个项目）
    for proj in projects:
        _agent_service.set_status(
            user_id=current_user.user_id,
            project_id=proj.project_id,
            status=body.status,
            max_load=body.max_load,
            auto_accept=body.auto_accept,
            skills=body.skills,
        )

    # 通过 WebSocket 推送状态变更
    try:
        from src.api.ws import notify_agent_status
        for proj in projects:
            await notify_agent_status(proj.project_id, current_user.user_id, body.status)
    except Exception as e:
        logger.warning(f"WebSocket 状态通知失败: {e}")

    # 如果客服上线，自动分配待处理转接请求
    if body.status in ("online", "busy"):
        for proj in projects:
            await _auto_assign_pending_handoffs(proj.project_id, current_user.user_id)

    return {
        "success": True,
        "user_id": current_user.user_id,
        "status": body.status,
    }


async def _auto_assign_pending_handoffs(project_id: str, agent_id: str):
    """自动分配待处理转接请求给刚上线的客服。

    按时间顺序分配最旧的待处理请求，一次最多分配 1 个。
    """
    from src.services.analytics_service import AnalyticsService
    from src.services.conversation_service import ConversationService

    analytics = AnalyticsService()
    conv_service = ConversationService()

    # 检查客服是否还可以接单
    agent_status = _agent_service.get_status(agent_id)
    if not agent_status:
        return
    if agent_status["current_load"] >= agent_status["max_load"]:
        return

    # 查找最旧的待处理转接请求
    handoffs = analytics.list_handoffs(project_id, status="pending", page=1, page_size=1)
    if not handoffs["items"]:
        return

    handoff = handoffs["items"][0]
    conv_id = handoff.get("conversation_id", "")
    if not conv_id:
        return

    try:
        conv_service.update_status(conv_id, "agent", agent_id)
        analytics.resolve_handoff(handoff["id"])
        _agent_service.increment_load(agent_id)
        logger.info(f"上线自动分配: handoff={handoff['id']}, agent={agent_id[:12]}")

        # WS 通知
        from src.api.ws import notify_conversation_status
        await notify_conversation_status(conv_id, "agent", agent_id)
    except Exception as e:
        logger.warning(f"上线自动分配失败: {e}")


@router.get("/api/agent/status")
async def get_agent_status(
    current_user: User = Depends(get_current_user),
):
    """获取当前客服的状态。"""
    status = _agent_service.get_status(current_user.user_id)
    if not status:
        return {
            "user_id": current_user.user_id,
            "status": "offline",
            "current_load": 0,
            "max_load": 5,
        }
    return status


@router.get("/api/projects/{project_id}/agents")
async def list_project_agents(
    project_id: str,
    online_only: bool = Query(False, description="仅列出在线客服"),
    current_user: User = Depends(get_current_user),
):
    """获取项目客服列表。"""
    # 验证项目所有权
    project = _project_service.get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")

    if online_only:
        agents = _agent_service.list_online_agents(project_id)
    else:
        agents = _agent_service.list_project_agents(project_id)

    return {"items": agents, "total": len(agents)}


# ================================================================
# WebSocket 通知辅助
# ================================================================


async def notify_agent_status(project_id: str, user_id: str, status: str):
    """通过 WebSocket 推送客服状态变更。"""
    from src.services.ws_manager import get_manager
    manager = get_manager()
    await manager.broadcast_to_project(
        project_id,
        {"type": "agent.status", "data": {"user_id": user_id, "status": status}},
        exclude_user=user_id,
    )