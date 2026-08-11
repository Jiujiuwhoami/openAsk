"""Widget Token 服务：签发和验证短期 JWT Token。

Widget Token 是嵌入脚本用来认证的短期令牌：
- 有效期短（默认 1 小时）
- 仅限聊天相关操作（type=widget 区分于用户 JWT type=access）
- 用于 X-Widget-Token 请求头
- 通过 WebSocket 连接时也使用此 token
"""

from datetime import datetime, timedelta
from typing import Optional

import jwt
from jwt.exceptions import InvalidTokenError

from src.utils.config import settings

# 默认过期时间（分钟）
_DEFAULT_EXPIRE_MINUTES = 60


def create_widget_token(project_id: str, expires_minutes: int = _DEFAULT_EXPIRE_MINUTES) -> str:
    """签发 Widget Token（JWT）。

    Token 包含:
      - sub: project_id（项目 ID）
      - type: "widget"（用于区分用户 JWT type="access"）
      - iat: 签发时间
      - exp: 过期时间

    Args:
        project_id: 项目 ID
        expires_minutes: 过期分钟数，默认 60

    Returns:
        JWT 字符串
    """
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    claims = {
        "sub": project_id,
        "type": "widget",
        "iat": datetime.utcnow(),
        "exp": expire,
    }
    return jwt.encode(claims, settings.auth.secret_key, algorithm=settings.auth.algorithm)


def verify_widget_token(token: str) -> Optional[str]:
    """验证 Widget Token。

    验证签名、过期时间、token 类型（必须是 type=widget）。

    Args:
        token: JWT 字符串

    Returns:
        验证通过返回 project_id，否则返回 None
    """
    if not token:
        return None
    try:
        payload = jwt.decode(
            token, settings.auth.secret_key, algorithms=[settings.auth.algorithm]
        )
        # 必须是 widget 类型的 token（区别于用户 JWT type="access"）
        if payload.get("type") != "widget":
            return None
        return payload.get("sub")
    except InvalidTokenError:
        # InvalidTokenError 是 PyJWT 所有解码失败异常的基类
        #（含过期、签名错误、格式错误等）
        return None