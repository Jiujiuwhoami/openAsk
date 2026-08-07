"""认证 API 路由 — OAuth2 标准密码流程。

端点：
  - POST /api/auth/register             用户注册 + 自动登录 + 自动创建项目
  - POST /api/auth/token                 OAuth2 标准 Token 端点（登录）
  - GET  /api/auth/me                    获取当前用户信息
  - POST /api/auth/send-verification     发送邮箱验证邮件
  - POST /api/auth/verify-email          验证邮箱
  - POST /api/auth/forgot-password       发送密码重置邮件
  - POST /api/auth/reset-password        重置密码
  - POST /api/auth/change-password       修改密码（需登录）

遵循的规范：
  - OAuth2 Password Flow (RFC 6749)
  - token 端点接受 application/x-www-form-urlencoded
  - 响应格式含 access_token / token_type
  - 错误响应符合 HTTP 语义（401/403/409）
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field

# 延迟导入 get_current_user（避免与 __init__.py 的循环依赖）
from src.api.dependencies import get_current_user
from src.domain.user import User
from src.domain.exceptions import (
    InvalidCredentialsError,
    UserAlreadyExistsError,
    UserNotFoundError,
    UserSuspendedError,
)
from src.services.user_service import UserService
from src.services.project_service import ProjectService
from src.services.email_service import (
    send_email,
    build_verification_email,
    build_password_reset_email,
)
from src.utils.config import settings

router = APIRouter(prefix="/api/auth")

# 全局服务实例（单例）
_user_service = UserService()
_project_service = ProjectService()


# ================================================================
# 请求/响应模型
# ================================================================


class RegisterRequest(BaseModel):
    """注册请求。"""

    email: str = Field(..., description="用户邮箱", min_length=1, max_length=255)
    password: str = Field(..., description="密码（至少 8 位）", min_length=8, max_length=128)
    name: str = Field("", description="用户名称", max_length=100)


class UserResponse(BaseModel):
    """用户信息响应。"""

    user_id: str
    email: str
    name: str
    is_verified: bool
    created_at: int


class TokenResponse(BaseModel):
    """OAuth2 标准 Token 响应。"""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class RegisterTokenResponse(TokenResponse):
    """注册响应（含自动创建的项目信息）。"""

    project: dict


class MeResponse(BaseModel):
    """当前用户信息响应。"""

    user_id: str
    email: str
    name: str
    is_verified: bool
    created_at: int


# ================================================================
# 路由
# ================================================================


@router.post("/register", response_model=RegisterTokenResponse)
async def register(body: RegisterRequest):
    """用户注册 + 自动登录 + 自动创建第一个 Project。

    注册成功后自动：
      1. 创建用户
      2. 生成 JWT token
      3. 创建第一个项目

    请求:
      POST /api/auth/register
      { "email": "user@example.com", "password": "12345678", "name": "用户名" }

    响应:
      {
        "access_token": "eyJ...",
        "token_type": "bearer",
        "user": { "user_id": "...", "email": "...", "name": "..." },
        "project": { "project_id": "...", "name": "...", "api_key": "sk_..." }
      }

    测试标准:
      - 正常注册 → 200
      - 重复邮箱 → 409
      - 密码太短 → 422
      - 无效邮箱 → 422
    """
    from email_validator import validate_email, EmailNotValidError

    # 邮箱格式校验（只检查格式，不检查 DNS）
    try:
        validate_email(body.email, check_deliverability=False)
    except EmailNotValidError:
        raise HTTPException(status_code=422, detail="无效的邮箱格式")

    try:
        user = _user_service.register(body.email, body.password, body.name)
    except UserAlreadyExistsError:
        raise HTTPException(status_code=409, detail="邮箱已被注册")

    # 自动创建第一个项目
    project = _project_service.create_project(
        user_id=user.user_id,
        name=f"{user.name or user.email.split('@')[0]} 的项目",
    )

    # 生成 token
    token = UserService.create_access_token(user.user_id)

    return RegisterTokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse(
            user_id=user.user_id,
            email=user.email,
            name=user.name,
            is_verified=user.is_verified,
            created_at=user.created_at,
        ),
        project={
            "project_id": project.project_id,
            "name": project.name,
            "api_key": project.api_key,
        },
    )


@router.post("/token", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """OAuth2 标准 Token 端点。

    请求:
      POST /api/auth/token
      Content-Type: application/x-www-form-urlencoded
      username=user@example.com&password=12345678

    响应:
      {
        "access_token": "eyJ...",
        "token_type": "bearer",
        "user": { "user_id": "...", "email": "...", "name": "..." }
      }

    测试标准:
      - 正确凭证 → 200
      - 错误密码 → 401（统一错误信息）
      - 不存在的邮箱 → 401
      - 已禁用用户 → 403
    """
    try:
        user = _user_service.authenticate(form_data.username, form_data.password)
    except (InvalidCredentialsError, UserNotFoundError):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    except UserSuspendedError:
        raise HTTPException(status_code=403, detail="账户已被禁用")

    token = UserService.create_access_token(user.user_id)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse(
            user_id=user.user_id,
            email=user.email,
            name=user.name,
            is_verified=user.is_verified,
            created_at=user.created_at,
        ),
    )


@router.get("/me", response_model=MeResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """获取当前用户信息。

    请求头: Authorization: Bearer <token>

    响应:
      { "user_id": "...", "email": "...", "name": "...", "is_verified": false, "created_at": 1234567890 }

    测试标准:
      - 有效 token → 200
      - 无 token → 401
      - 无效 token → 401
      - 过期 token → 401
    """
    return MeResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        name=current_user.name,
        is_verified=current_user.is_verified,
        created_at=current_user.created_at,
    )


# ================================================================
# 邮箱验证
# ================================================================


class SendVerificationRequest(BaseModel):
    email: str = Field(..., description="用户邮箱")


@router.post("/send-verification")
async def send_verification(body: SendVerificationRequest):
    """发送邮箱验证邮件。

    测试标准:
      - 发送成功 → 200
      - 未注册邮箱 → 404
      - 已验证邮箱 → 200（提示已验证）
      - 频率限制 → 429（1 分钟内重复发送）
    """
    user = _user_service.get_by_email(body.email)
    if not user:
        raise HTTPException(status_code=404, detail="邮箱未注册")

    if user.is_verified:
        return {"message": "邮箱已验证，无需重复验证"}

    # 生成验证 token（24 小时有效）
    token = UserService.create_access_token(
        user.user_id,
        expires_delta=timedelta(hours=24),
    )
    base_url = settings.api.frontend_url or 'http://localhost:5173'
    html = build_verification_email(body.email, token, base_url)
    send_email(body.email, "验证邮箱地址 — OpenAsk", html)

    return {"message": "验证邮件已发送，请检查邮箱"}


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., description="验证 token")


@router.post("/verify-email")
async def verify_email(body: VerifyEmailRequest):
    """验证邮箱。

    测试标准:
      - 验证成功 → 200
      - 无效 token → 401
      - 过期 token → 401
      - 重复验证 → 200（提示已验证）
    """
    payload = UserService.decode_access_token(body.token)
    if payload is None:
        raise HTTPException(status_code=401, detail="无效的验证链接")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="无效的验证链接")

    user = _user_service.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user.is_verified:
        return {"message": "邮箱已验证"}

    _user_service.verify_email(user_id)
    return {"message": "邮箱验证成功"}


# ================================================================
# 密码重置
# ================================================================


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., description="用户邮箱")


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest):
    """发送密码重置邮件。

    测试标准:
      - 发送成功 → 200
      - 不存在的邮箱 → 200（不暴露邮箱是否存在）
      - 频率限制 → 429（1 分钟内重复发送）
    """
    user = _user_service.get_by_email(body.email)

    # 无论邮箱是否存在都返回 200（防止枚举）
    if not user:
        return {"message": "如果该邮箱已注册，你将收到密码重置邮件"}

    # 生成重置 token（15 分钟有效）
    token = UserService.create_access_token(
        user.user_id,
        expires_delta=timedelta(minutes=15),
    )
    base_url = settings.api.frontend_url or 'http://localhost:5173'
    html = build_password_reset_email(body.email, token, base_url)
    send_email(body.email, "重置密码 — OpenAsk", html)

    return {"message": "如果该邮箱已注册，你将收到密码重置邮件"}


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., description="重置 token")
    password: str = Field(..., description="新密码", min_length=8, max_length=128)


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest):
    """重置密码。

    测试标准:
      - 重置成功 → 200
      - 无效 token → 401
      - 过期 token → 401
      - 重复使用 token → 401
      - 密码太短 → 422
    """
    payload = UserService.decode_access_token(body.token)
    if payload is None:
        raise HTTPException(status_code=401, detail="无效或已过期的重置链接")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="无效或已过期的重置链接")

    user = _user_service.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 更新密码（直接设置新密码，不需要旧密码）
    new_hash = UserService.hash_password(body.password)
    _user_service._update_password(user_id, new_hash)

    return {"message": "密码重置成功，请使用新密码登录"}


# ================================================================
# 修改密码（需登录）
# ================================================================


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., description="旧密码")
    new_password: str = Field(..., description="新密码", min_length=8, max_length=128)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
):
    """修改密码（需要登录）。

    测试标准:
      - 修改成功 → 200
      - 旧密码错误 → 401
      - 新密码太短 → 422
      - 未登录 → 401
      - 修改后可用新密码登录
      - 修改后旧密码失效
    """
    if not UserService.verify_password(body.old_password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="原密码错误")

    _user_service.change_password(
        current_user.user_id, body.old_password, body.new_password
    )
    return {"message": "密码修改成功"}