"""FastAPI 依赖注入：OAuth2 认证 + Project 鉴权。

提供两个核心依赖：
  - get_current_user: 从 JWT Bearer token 解析当前用户
  - get_current_project: 从 X-API-Key 解析当前项目

用法：
  @router.get("/api/projects")
  async def list_projects(current_user=Depends(get_current_user)):
      ...

  @router.post("/api/chat")
  async def chat(project=Depends(get_current_project)):
      ...
"""

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer

from src.domain.user import User
from src.domain.project import Project
from src.services.user_service import UserService
from src.services.project_service import ProjectService

# OAuth2 标准：自动从 Authorization: Bearer 提取 token
# tokenUrl 用于 Swagger UI 的 "Authorize" 按钮自动填充
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


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

    用于 /api/chat, /api/knowledge, /api/search 等业务路由，
    这些路由被嵌入脚本或第三方客户端通过 API Key 调用。

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


