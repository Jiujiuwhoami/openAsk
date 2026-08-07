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
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from src.api.dependencies import get_current_user
from src.api.routes import resolve_project
from src.domain.user import User
from src.domain.project import Project
from src.services.analytics_service import AnalyticsService
from src.services.project_service import ProjectService
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()
_analytics = AnalyticsService()
_project_service = ProjectService()


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


class HandoffRequest(BaseModel):
    conversation_id: str = Field("", description="会话 ID")
    query: str = Field(..., description="用户问题", max_length=2000)
    contact_email: str = Field("", description="联系邮箱")
    contact_phone: str = Field("", description="联系电话")
    note: str = Field("", description="补充说明", max_length=1000)


@router.post("/api/projects/{project_id}/handoff")
async def submit_handoff(
    project_id: str,
    body: HandoffRequest,
    project: Project = Depends(resolve_project),
):
    """提交人工客服转接请求。

    使用 X-API-Key 鉴权（嵌入脚本和管理面板均可调用）。
    验证 API Key 对应的项目与路径中的 project_id 一致，
    然后记录请求并发送邮件通知项目所有者。
    """
    # 验证 API Key 的项目与路径一致
    if project.project_id != project_id:
        raise HTTPException(status_code=403, detail="无权为此项目提交转接请求")

    # 记录转接请求
    req_id = _analytics.record_handoff(
        project_id=project_id,
        query=body.query,
        conversation_id=body.conversation_id,
        contact_email=body.contact_email,
        contact_phone=body.contact_phone,
        note=body.note,
    )

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

    return {"message": "转接请求已提交", "request_id": req_id}


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