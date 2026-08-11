"""分析 API 路由。

端点：
  - GET    /api/projects/{id}/logs         问答日志列表
  - GET    /api/projects/{id}/logs/export   导出日志
  - GET    /api/projects/{id}/analytics/trends       趋势
  - GET    /api/projects/{id}/analytics/top-questions 热门问题
  - POST   /api/projects/{id}/feedback     提交反馈
  - GET    /api/projects/{id}/analytics/satisfaction  满意度
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from src.api.dependencies import get_current_user, resolve_widget_project
from src.api.routes import resolve_project
from src.domain.user import User
from src.domain.project import Project
from src.services.analytics_service import AnalyticsService
from src.services.agent_service import AgentService
from src.services.conversation_service import ConversationService
from src.services.project_service import ProjectService
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()
_analytics = AnalyticsService()
_agent_service = AgentService()
_conv_service = ConversationService()
_project_service = ProjectService()


class BatchDeleteRequest(BaseModel):
    """批量删除日志请求。"""

    log_ids: list[int]


def _verify_project_owner(project_id: str, user: User):
    """验证用户是项目所有者。"""
    project = _project_service.get_by_id(project_id)
    if not project or project.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")


# ================================================================
# 日志
# ================================================================


@router.get("/api/projects/{project_id}/logs")
async def get_logs(
    project_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query(""),
    current_user: User = Depends(get_current_user),
):
    """获取问答日志列表（分页）。"""
    _verify_project_owner(project_id, current_user)
    return _analytics.get_logs(project_id, page=page, page_size=page_size, search=search)


@router.delete("/api/projects/{project_id}/logs")
async def delete_logs(
    project_id: str,
    current_user: User = Depends(get_current_user),
):
    """清空项目的所有问答日志。"""
    _verify_project_owner(project_id, current_user)
    count = _analytics.delete_logs(project_id)
    return {"message": f"已删除 {count} 条日志", "deleted": count}


@router.post("/api/projects/{project_id}/logs/batch-delete")
async def batch_delete_logs(
    project_id: str,
    body: BatchDeleteRequest,
    current_user: User = Depends(get_current_user),
):
    """批量删除日志。"""
    _verify_project_owner(project_id, current_user)
    if not body.log_ids:
        raise HTTPException(status_code=400, detail="log_ids 不能为空")
    count = _analytics.delete_logs_batch(body.log_ids, project_id)
    return {"message": f"已删除 {count} 条日志", "deleted": count}


@router.delete("/api/projects/{project_id}/logs/{log_id}")
async def delete_log(
    project_id: str,
    log_id: int,
    current_user: User = Depends(get_current_user),
):
    """删除单条问答日志。"""
    _verify_project_owner(project_id, current_user)
    deleted = _analytics.delete_log(log_id, project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="日志不存在")
    return {"message": "日志已删除"}


@router.get("/api/projects/{project_id}/logs/export")
async def export_logs(
    project_id: str,
    format: str = Query("csv", regex="^(csv|json)$"),
    current_user: User = Depends(get_current_user),
):
    """导出问答日志。"""
    _verify_project_owner(project_id, current_user)
    content = _analytics.export_logs(project_id, format=format)
    media_type = "text/csv" if format == "csv" else "application/json"
    filename = f"chat_logs_{project_id}_{datetime.now().strftime('%Y%m%d')}.{format}"
    return PlainTextResponse(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ================================================================
# 趋势
# ================================================================


@router.get("/api/projects/{project_id}/analytics/trends")
async def get_trends(
    project_id: str,
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
):
    """获取问答量趋势。"""
    _verify_project_owner(project_id, current_user)
    return _analytics.get_trends(project_id, days=days)


@router.get("/api/projects/{project_id}/analytics/top-questions")
async def get_top_questions(
    project_id: str,
    limit: int = Query(10, ge=1, le=50),
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
):
    """获取热门问题 Top N。"""
    _verify_project_owner(project_id, current_user)
    return _analytics.get_top_questions(project_id, limit=limit, days=days)


# ================================================================
# 反馈
# ================================================================


class FeedbackRequest(BaseModel):
    log_id: int = Field(..., description="日志 ID")
    rating: str = Field(..., pattern="^(good|bad)$", description="评价: good/bad")


@router.post("/api/projects/{project_id}/feedback")
async def submit_feedback(
    project_id: str,
    body: FeedbackRequest,
    current_user: User = Depends(get_current_user),
):
    """提交问答反馈。"""
    _verify_project_owner(project_id, current_user)
    _analytics.record_feedback(body.log_id, project_id, body.rating)
    return {"message": "反馈已提交"}


@router.get("/api/projects/{project_id}/analytics/satisfaction")
async def get_satisfaction(
    project_id: str,
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
):
    """获取满意度统计。"""
    _verify_project_owner(project_id, current_user)
    return _analytics.get_satisfaction(project_id, days=days)


@router.get("/api/projects/{project_id}/analytics/gaps")
async def get_gaps(
    project_id: str,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """获取知识库缺口分析：AI 答不上来的问题。"""
    _verify_project_owner(project_id, current_user)
    return _analytics.get_gaps(project_id, days=days, limit=limit)


# ================================================================
# 人工客服转接
# ================================================================


class CsatRequest(BaseModel):
    conversation_id: str = Field("", description="会话 ID")
    rating: int = Field(..., description="评分 1-5", ge=1, le=5)
    tags: Optional[List[str]] = Field(None, description="评价标签")
    feedback: str = Field("", description="文字反馈", max_length=1000)


class CsatStatsResponse(BaseModel):
    total: int = 0
    avg_rating: float = 0.0
    positive_rate: float = 0.0
    negative_rate: float = 0.0


@router.post("/api/feedback/csat")
async def submit_csat(
    body: CsatRequest,
    project: Project = Depends(resolve_widget_project),
):
    """提交 CSAT 满意度评价（X-API-Key 鉴权，Widget 调用）。"""
    conv = _conv_service.get_conversation(body.conversation_id)
    agent_id = conv.agent_id if conv else ""
    req_id = _analytics.record_csat(
        conversation_id=body.conversation_id,
        project_id=project.project_id,
        rating=body.rating,
        agent_id=agent_id,
        tags=body.tags,
        feedback=body.feedback,
    )
    return {"success": True, "request_id": req_id, "message": "感谢您的评价！"}


@router.get("/api/projects/{project_id}/analytics/csat", response_model=CsatStatsResponse)
async def get_csat_stats(
    project_id: str,
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
):
    """获取 CSAT 满意度统计。"""
    _verify_project_owner(project_id, current_user)
    return _analytics.get_csat_stats(project_id, days=days)


@router.get("/api/projects/{project_id}/analytics/agents")
async def get_agent_performance(
    project_id: str,
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
):
    """获取客服绩效统计。"""
    _verify_project_owner(project_id, current_user)
    return _analytics.get_agent_performance(project_id, days=days)


@router.get("/api/projects/{project_id}/analytics/csat/list")
async def list_csat(
    project_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """获取 CSAT 评价列表。"""
    _verify_project_owner(project_id, current_user)
    return _analytics.list_csat(project_id, page=page, page_size=page_size)


class HandoffRequest(BaseModel):
    conversation_id: str = Field("", description="会话 ID")
    query: str = Field(..., description="用户问题", max_length=2000)
    contact_email: str = Field("", description="联系邮箱")
    contact_phone: str = Field("", description="联系电话")
    note: str = Field("", description="补充说明", max_length=1000)
    reason: str = Field("user_initiated", description="转接原因: user_initiated / system_suggested / auto_escalation")
    priority: int = Field(0, description="优先级: 0=普通, 1=高, 2=紧急", ge=0, le=2)


@router.post("/api/projects/{project_id}/handoff")
async def submit_handoff(
    project_id: str,
    body: HandoffRequest,
    project: Project = Depends(resolve_widget_project),
):
    """提交人工客服转接请求。

    使用 X-API-Key 鉴权（嵌入脚本和管理面板均可调用）。
    验证 API Key 对应的项目与路径中的 project_id 一致，
    然后记录请求，将会话状态标记为 queuing，并发送邮件通知项目所有者。
    """
    # 验证 API Key 的项目与路径一致
    if project.project_id != project_id:
        raise HTTPException(status_code=403, detail="无权为此项目提交转接请求")

    # 将会话状态标记为 queuing（如果有 conversation_id）
    if body.conversation_id:
        _conv_service.update_status(body.conversation_id, "queuing", "")

    # 记录转接请求
    req_id = _analytics.record_handoff(
        project_id=project_id,
        query=body.query,
        conversation_id=body.conversation_id,
        contact_email=body.contact_email,
        contact_phone=body.contact_phone,
        note=body.note,
        reason=body.reason,
        priority=body.priority,
    )

    # 计算排队位置
    queue_info = _analytics.get_queue_position(project_id, req_id)

    # 尝试自动分配：如果有在线客服，自动接管（轮询策略）
    auto_assigned = False
    available = _agent_service.get_available_agent(project_id, strategy="round_robin")
    if available and body.conversation_id:
        try:
            _conv_service.update_status(body.conversation_id, "agent", available["user_id"])
            _analytics.resolve_handoff(req_id)
            _agent_service.increment_load(available["user_id"])
            auto_assigned = True
            logger.info(f"自动分配: handoff={req_id}, agent={available['user_id'][:12]}")
            # 通知客服有新分配
            from src.api.ws import notify_conversation_status
            await notify_conversation_status(body.conversation_id, "agent", available["user_id"])
        except Exception as e:
            logger.warning(f"自动分配失败: {e}")

    # 未自动分配时，发送自动回复消息
    if not auto_assigned and body.conversation_id:
        _conv_service.add_message(
            body.conversation_id,
            "system",
            "当前暂无在线客服，请稍候。我们会在第一时间为您分配客服人员。",
            message_type="text",
        )

    # 推送新转接请求通知给在线客服（未自动分配时才推送）
    if not auto_assigned:
        try:
            from src.api.ws import notify_handoff_new
            await notify_handoff_new(project_id, {
                "id": req_id,
                "project_id": project_id,
                "conversation_id": body.conversation_id,
                "query": body.query,
                "contact_email": body.contact_email,
                "contact_phone": body.contact_phone,
                "note": body.note,
                "reason": body.reason,
                "priority": body.priority,
                "status": "pending",
            })
        except Exception as e:
            logger.warning(f"WebSocket 转接通知失败: {e}")

    # 发送邮件通知
    try:
        from src.services.user_service import UserService
        from src.services.email_service import send_email, build_handoff_email

        user = UserService().get_by_id(project.user_id)
        if user and user.email:
            html = build_handoff_email(
                email=user.email,
                project_name=project.name,
                query=body.query,
                contact_email=body.contact_email,
                contact_phone=body.contact_phone,
                note=body.note,
            )
            send_email(
                to=user.email,
                subject=f"[OpenAsk] 新的人工客服转接请求 — {project.name}",
                html_content=html,
            )
    except Exception as e:
        logger.warning(f"发送转接通知邮件失败: {e}")

    return {
        "message": "转接请求已提交",
        "request_id": req_id,
        "queue_position": queue_info["position"],
        "estimated_wait_seconds": queue_info["estimated_wait_seconds"],
        "status": "queuing",
    }


@router.post("/api/projects/{project_id}/handoff/cancel")
async def cancel_handoff(
    project_id: str,
    body: HandoffRequest,
    project: Project = Depends(resolve_widget_project),
):
    """取消排队中的转接请求，将会话恢复为 AI 模式。

    使用 X-API-Key 鉴权（嵌入脚本调用）。
    """
    if project.project_id != project_id:
        raise HTTPException(status_code=403, detail="无权为此项目取消转接请求")

    if not body.conversation_id:
        raise HTTPException(status_code=400, detail="需要 conversation_id")

    # 取消转接请求
    cancelled = _analytics.cancel_handoff(body.conversation_id)

    # 恢复会话状态为 active
    _conv_service.update_status(body.conversation_id, "active", "")

    return {
        "success": cancelled,
        "message": "已取消转接请求" if cancelled else "未找到待处理的转接请求",
        "status": "active",
    }


@router.get("/api/projects/{project_id}/handoffs")
async def list_handoffs(
    project_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, regex="^(pending|resolved|closed)$"),
    current_user: User = Depends(get_current_user),
):
    """获取人工客服转接请求列表。"""
    _verify_project_owner(project_id, current_user)
    return _analytics.list_handoffs(project_id, page=page, page_size=page_size, status=status)


@router.post("/api/projects/{project_id}/handoffs/{handoff_id}/resolve")
async def resolve_handoff(
    project_id: str,
    handoff_id: int,
    current_user: User = Depends(get_current_user),
):
    """标记转接请求为已处理。"""
    _verify_project_owner(project_id, current_user)
    success = _analytics.resolve_handoff(handoff_id)
    if not success:
        raise HTTPException(status_code=404, detail="转接请求不存在")
    return {"message": "已标记为已处理"}