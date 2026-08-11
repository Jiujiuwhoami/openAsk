"""Widget Session 端点：为嵌入脚本签发短期 Token。

端点：
  POST /api/widget/session
  请求体: { "project_id": "proj_xxx" }
  响应:   { "token": "eyJ...", "expires_in": 3600, "project_id": "proj_xxx" }

域名校验：
  - 校验 Origin 头（浏览器必须带）
  - Origin host 必须在 Project 的 allowed_domains 白名单中
  - 空白名单 → fail closed（403）
  - 管理面板前端 origin 始终允许（预览用）
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.services.project_service import ProjectService
from src.services.widget_token import create_widget_token
from src.utils.domain import parse_host, is_domain_allowed
from src.utils.config import settings

router = APIRouter(prefix="/api/widget")
_project_service = ProjectService()


class WidgetSessionRequest(BaseModel):
    project_id: str = Field(..., description="项目 ID", min_length=1)


class WidgetSessionResponse(BaseModel):
    token: str
    expires_in: int
    project_id: str


@router.post("/session", response_model=WidgetSessionResponse)
async def widget_session(body: WidgetSessionRequest, request: Request):
    """换取 Widget 短期 Token。

    校验流程：
    1. Project 存在且 active
    2. Origin 头存在（浏览器请求必须带）
    3. Origin host 在白名单（project.allowed_domains ∪ frontend_url）
    4. 白名单为空且 origin 非 frontend → 403

    测试标准:
      - 有效项目 + 允许的 origin → 200 + token
      - 无 Origin 头 → 403
      - 不允许的 origin → 403
      - 空白名单 + 非 frontend → 403
      - 空白名单 + frontend origin → 200（预览场景）
      - 不存在的项目 → 404
      - 已删除项目 → 403
    """
    project = _project_service.get_by_id(body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not project.is_active:
        raise HTTPException(status_code=403, detail="项目已被禁用")

    # 获取来源 origin
    origin = request.headers.get("origin", "").strip()
    if not origin:
        raise HTTPException(
            status_code=403,
            detail="缺少 Origin 头，Widget 只能从浏览器嵌入",
        )

    host = parse_host(origin)
    if not host:
        raise HTTPException(status_code=403, detail="无法解析 Origin 头")

    # 构建允许的域名列表：白名单 + frontend_url（预览用）
    frontend_host = parse_host(settings.api.frontend_url)
    allowed = list(project.allowed_domains)
    if frontend_host:
        allowed.append(frontend_host)

    # 校验域名
    if not is_domain_allowed(host, allowed):
        if not project.allowed_domains:
            raise HTTPException(
                status_code=403,
                detail="请在项目设置中配置允许嵌入的域名（域名白名单）",
            )
        raise HTTPException(
            status_code=403,
            detail=f"域名 {host} 未授权，请检查项目域名白名单",
        )

    # 签发短期 token
    expires_minutes = settings.auth.widget_token_expire_minutes
    token = create_widget_token(project.project_id, expires_minutes=expires_minutes)

    return WidgetSessionResponse(
        token=token,
        expires_in=expires_minutes * 60,
        project_id=project.project_id,
    )