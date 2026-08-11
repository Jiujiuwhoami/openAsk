"""FastAPI 依赖注入：OAuth2 认证 + Project 鉴权。

提供三个核心依赖：
  - get_current_user: 从 JWT Bearer token 解析当前用户
  - get_current_project: 从 X-API-Key 解析当前项目（管理用途）
  - resolve_widget_project: 优先 X-Widget-Token，回退 X-API-Key（Widget 用途）

用法：
  @router.get("/api/projects")
  async def list_projects(current_user=Depends(get_current_user)):
      ...

  @router.post("/api/chat")
  async def chat(project=Depends(resolve_widget_project)):
      ...
"""

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer

from src.domain.user import User
from src.domain.project import Project
from src.services.user_service import UserService
from src.services.project_service import ProjectService
from src.services.widget_token import verify_widget_token
from src.utils.domain import parse_host, is_domain_allowed
from src.utils.config import settings

# OAuth2 标准：自动从 Authorization: Bearer 提取 token
# tokenUrl 用于 Swagger UI 的 "Authorize" 按钮自动填充
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

# 模块级 ProjectService 实例（测试可覆盖此引用）
_project_service = ProjectService()


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """OAuth2 标准依赖：从 JWT token 解析当前用户。

    流程：
      1. oauth2_scheme 自动提取 Authorization: Bearer <token>
      2. 解码 JWT → user_id (sub claim)
      3. 查询数据库 → User
      4. 检查用户是否 active

    Returns:
        当前登录用户

    Raises:
        HTTPException 401: 无 token、无效 token、过期 token
        HTTPException 403: 用户已禁用
    """
    payload = UserService.decode_access_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="无效的访问令牌")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="无效的访问令牌")

    user = UserService().get_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账户已被禁用")

    return user


async def get_current_project(request: Request) -> Project:
    """从 X-API-Key 解析当前 Project。

    用于 /api/knowledge, /api/search 等管理路由，
    这些路由需要完整的 API Key 鉴权（知识库 CRUD 等敏感操作）。

    Returns:
        当前请求对应的 Project

    Raises:
        HTTPException 401: 缺少 Key、无效 Key
        HTTPException 403: 项目已被禁用
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="缺少 X-API-Key")

    project = ProjectService().get_by_api_key(api_key)
    if project is None:
        raise HTTPException(status_code=401, detail="无效的 API Key")
    if not project.is_active:
        raise HTTPException(status_code=403, detail="项目已被禁用")

    request.state.project = project
    return project


async def resolve_widget_project(request: Request) -> Project:
    """Widget 鉴权依赖：优先 X-Widget-Token，回退 X-API-Key。

    用于 /api/chat, /api/chat/poll, /api/chat/message, /api/feedback/csat,
    /api/projects/{id}/handoff 等 Widget 调用的路由。

    鉴权流程：
      1. 优先检查 X-Widget-Token（短期 token，聊天权限）
         - 验证 token 签名和有效期
         - 校验 Origin 是否在域名白名单中
      2. 回退 X-API-Key（永久 key，完整权限，管理面板用）
         - 不经 Origin 校验（管理面板内部使用）

    Returns:
        当前请求对应的 Project

    Raises:
        HTTPException 401: 无 token、无效 token
        HTTPException 403: 域名未授权、项目已禁用
    """
    widget_token = request.headers.get("X-Widget-Token")
    api_key = request.headers.get("X-API-Key")

    if widget_token:
        project_id = verify_widget_token(widget_token)
        if not project_id:
            raise HTTPException(status_code=401, detail="无效的 Widget Token")

        project = _project_service.get_by_id(project_id)
        if not project:
            raise HTTPException(status_code=401, detail="项目不存在")
        if not project.is_active:
            raise HTTPException(status_code=403, detail="项目已被禁用")

        # Origin 校验：仅对 Widget Token 请求（非 X-API-Key 回退）
        origin = request.headers.get("origin", "").strip()
        if not origin:
            raise HTTPException(status_code=403, detail="缺少 Origin 头")

        host = parse_host(origin)
        frontend_host = parse_host(settings.api.frontend_url)
        allowed = list(project.allowed_domains)
        if frontend_host:
            allowed.append(frontend_host)

        if not is_domain_allowed(host, allowed):
            if not project.allowed_domains:
                raise HTTPException(
                    status_code=403,
                    detail="请在项目设置中配置允许嵌入的域名",
                )
            raise HTTPException(
                status_code=403,
                detail=f"域名 {host} 未授权",
            )

        request.state.project = project
        return project

    elif api_key:
        # 回退到 X-API-Key 路径（管理面板内部调用）
        project = _project_service.get_by_api_key(api_key)
        if not project:
            raise HTTPException(status_code=401, detail="无效的 API Key")
        if not project.is_active:
            raise HTTPException(status_code=403, detail="项目已被禁用")

        request.state.project = project
        return project

    raise HTTPException(status_code=401, detail="缺少认证信息（需要 X-Widget-Token 或 X-API-Key）")


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """管理员权限依赖：检查当前用户是否为管理员。

    Raises:
        HTTPException 403: 非管理员用户
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_user