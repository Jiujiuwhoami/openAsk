"""项目 API 路由。

需要用户登录（JWT）的所有端点。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from src.api.dependencies import get_current_user
from src.domain.user import User
from src.domain.exceptions import ProjectNotFoundError
from src.services.project_service import ProjectService
from src.services.embed_script import generate_embed_script
from src.utils.config import settings

router = APIRouter(prefix="/api/projects")
_project_service = ProjectService()


# ================================================================
# 请求/响应模型
# ================================================================


class CreateProjectRequest(BaseModel):
    name: str = Field(..., description="项目名称", min_length=1, max_length=200)


class UpdateProjectRequest(BaseModel):
    name: Optional[str] = Field(None, description="项目名称", max_length=200)
    llm_api_key: Optional[str] = Field(None, description="LLM API Key")
    llm_api_base: Optional[str] = Field(None, description="LLM API Base")
    llm_model: Optional[str] = Field(None, description="LLM 模型")
    llm_timeout: Optional[int] = Field(None, ge=5, le=120, description="LLM 超时秒数")
    rate_limit_per_user: Optional[str] = Field(None, description="每用户限流")
    rate_limit_global: Optional[str] = Field(None, description="全局限流")
    system_prompt: Optional[str] = Field(None, description="自定义系统 Prompt")
    language: Optional[str] = Field(None, pattern="^(zh|en)$", description="回答语言: zh/en")
    allowed_domains: Optional[list[str]] = Field(None, description="域名白名单", examples=[["example.com", "shop.example.com"]])


class ProjectResponse(BaseModel):
    project_id: str
    name: str
    api_key: str
    status: str
    created_at: int


class ProjectDetailResponse(BaseModel):
    project_id: str
    name: str
    status: str
    llm_api_base: str
    llm_model: str
    llm_timeout: int
    rate_limit_per_user: str
    rate_limit_global: str
    system_prompt: str
    language: str
    allowed_domains: list[str] = Field(default_factory=list, description="域名白名单")
    created_at: int
    updated_at: int


class ProjectStatsResponse(BaseModel):
    project_id: str
    document_count: int
    total_calls: int
    prompt_tokens: int
    completion_tokens: int
    cache_hit_rate: float
    created_at: int
    last_request: int


class EmbedScriptResponse(BaseModel):
    script: str


# ================================================================
# 路由
# ================================================================


@router.get("", response_model=list[ProjectResponse])
async def list_projects(current_user: User = Depends(get_current_user)):
    """获取当前用户的所有项目列表。

    测试标准:
      - 已登录用户 → 200 + 项目列表
      - 新注册用户 → 200 + 1 个项目
      - 未登录 → 401
    """
    projects = _project_service.list_by_user(current_user.user_id)
    return [
        ProjectResponse(
            project_id=p.project_id,
            name=p.name,
            api_key=p.api_key,
            status=p.status,
            created_at=p.created_at,
        )
        for p in projects
    ]


@router.post("", response_model=ProjectResponse)
async def create_project(
    body: CreateProjectRequest,
    current_user: User = Depends(get_current_user),
):
    """创建新项目。

    测试标准:
      - 创建成功 → 200 + 返回项目信息（含完整 API Key）
      - 名称为空 → 422
      - 未登录 → 401
    """
    project = _project_service.create_project(
        user_id=current_user.user_id,
        name=body.name,
    )
    return ProjectResponse(
        project_id=project.project_id,
        name=project.name,
        api_key=project.api_key,
        status=project.status,
        created_at=project.created_at,
    )


@router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
):
    """获取项目详情。

    测试标准:
      - 自己的项目 → 200
      - 别人的项目 → 404
      - 不存在的项目 → 404
    """
    project = _project_service.get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")

    return ProjectDetailResponse(
        project_id=project.project_id,
        name=project.name,
        status=project.status,
        llm_api_base=project.llm_api_base,
        llm_model=project.llm_model,
        llm_timeout=project.llm_timeout,
        rate_limit_per_user=project.rate_limit_per_user,
        rate_limit_global=project.rate_limit_global,
        system_prompt=project.system_prompt,
        language=project.language,
        allowed_domains=project.allowed_domains,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.put("/{project_id}")
async def update_project(
    project_id: str,
    body: UpdateProjectRequest,
    current_user: User = Depends(get_current_user),
):
    """更新项目配置。

    测试标准:
      - 更新成功 → 200
      - 更新别人的项目 → 404
    """
    project = _project_service.get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")

    updates = body.model_dump(exclude_none=True)
    if updates:
        _project_service.update_project(project_id, **updates)

    return {"success": True, "project_id": project_id}


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
):
    """删除项目（软删除）。

    测试标准:
      - 删除成功 → 200 + success: true
      - 删除后列表不再显示
      - 删除后 API Key 失效
    """
    project = _project_service.get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")

    _project_service.delete_project(project_id)
    return {"success": True, "message": "项目已删除"}


@router.post("/{project_id}/rotate-key")
async def rotate_api_key(
    project_id: str,
    current_user: User = Depends(get_current_user),
):
    """轮换 API Key。

    测试标准:
      - 轮换后新 key 可用，旧 key 失效
    """
    project = _project_service.get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")

    new_key = _project_service.rotate_api_key(project_id)
    return {"api_key": new_key}


@router.get("/{project_id}/stats", response_model=ProjectStatsResponse)
async def get_project_stats(
    project_id: str,
    current_user: User = Depends(get_current_user),
):
    """获取项目使用统计。

    测试标准:
      - 返回正确的统计数字
      - 新项目返回 0
    """
    project = _project_service.get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")

    stats = _project_service.get_stats(project_id)

    return ProjectStatsResponse(
        project_id=project_id,
        document_count=stats.get("document_count", 0),
        total_calls=stats.get("total_calls", 0),
        prompt_tokens=stats.get("prompt_tokens", 0),
        completion_tokens=stats.get("completion_tokens", 0),
        cache_hit_rate=stats.get("cache_hit_rate", 0.0),
        created_at=project.created_at,
        last_request=stats.get("last_call_at", 0),
    )


@router.get("/{project_id}/embed-script", response_model=EmbedScriptResponse)
async def get_embed_script(
    project_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """获取嵌入脚本代码。

    自动从请求头获取 API 地址，确保嵌入脚本在任何部署环境下都能正确指向后端。
    """
    project = _project_service.get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 从 frontend_url 推导 API 地址（同一主机，后端端口）
    # 解决 Vite 代理导致 Host 头变为 localhost:8000 的问题
    from urllib.parse import urlparse
    parsed = urlparse(settings.api.frontend_url)
    if parsed.hostname:
        api_base = f"{parsed.scheme}://{parsed.hostname}:{settings.api.port}"
    else:
        # 回退到请求头推导（兼容反向代理 X-Forwarded-*）
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost:8000"))
        api_base = f"{scheme}://{host}"

    # v2 安全版：不再嵌入 API Key，只含 project_id，运行时换取短期 Widget Token
    script = generate_embed_script(project_id, api_base=api_base)
    return EmbedScriptResponse(script=script)