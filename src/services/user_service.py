"""用户服务：注册、登录、JWT 管理。

使用 passlib 进行密码哈希，python-jose 进行 JWT Token 管理。
遵循 OAuth2 密码流程标准。
"""

import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional

import jwt
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext

from src.domain.user import User
from src.domain.exceptions import (
    UserNotFoundError,
    UserAlreadyExistsError,
    InvalidCredentialsError,
    UserSuspendedError,
)
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# passlib 标准密码上下文（自动管理哈希算法和盐值）
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT DEFAULT '',
    is_verified INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
"""


def _user_from_row(row: dict) -> User:
    """从 SQLite 行记录构建 User 实例。"""
    return User(
        user_id=row["user_id"],
        email=row["email"],
        password_hash=row["password_hash"],
        name=row["name"],
        is_verified=bool(row["is_verified"]),
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class UserService:
    """用户管理服务：注册、登录、JWT 管理。"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or "data/users.db"
        self._lock = threading.RLock()
        self._ensure_db()

    def _ensure_db(self) -> None:
        """确保数据库目录和表存在。"""
        dir_path = os.path.dirname(self._db_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with self._lock:
            conn = self._get_connection()
            try:
                conn.executescript(_INIT_SQL)
                conn.commit()
            finally:
                conn.close()
            logger.info(f"用户数据库已初始化: {self._db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接。"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ---- 密码管理 ----

    @staticmethod
    def hash_password(password: str) -> str:
        """passlib 标准哈希。"""
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """passlib 标准验证。"""
        return pwd_context.verify(plain_password, hashed_password)

    # ---- JWT 管理 ----

    @staticmethod
    def create_access_token(user_id: str, expires_delta: Optional[timedelta] = None) -> str:
        """生成符合 OAuth2 标准的 JWT access token。"""
        expire = datetime.utcnow() + (
            expires_delta or timedelta(minutes=settings.auth.access_token_expire_minutes)
        )
        claims = {
            "sub": user_id,
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "access",
        }
        return jwt.encode(claims, settings.auth.secret_key, algorithm=settings.auth.algorithm)

    @staticmethod
    def decode_access_token(token: str) -> Optional[dict]:
        """解码并验证 JWT token。"""
        try:
            payload = jwt.decode(
                token, settings.auth.secret_key, algorithms=[settings.auth.algorithm]
            )
            return payload
        except InvalidTokenError:
            return None

    # ---- 用户 CRUD ----

    def register(self, email: str, password: str, name: str = "") -> User:
        """注册新用户。

        Args:
            email: 用户邮箱
            password: 明文密码
            name: 用户名称

        Returns:
            创建的 User 实例

        Raises:
            UserAlreadyExistsError: 邮箱已被注册
        """
        existing = self.get_by_email(email)
        if existing:
            raise UserAlreadyExistsError(f"邮箱已被注册: {email}")

        user_id = f"user_{secrets.token_hex(8)}"
        password_hash = self.hash_password(password)
        now = int(datetime.utcnow().timestamp())

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """INSERT INTO users (user_id, email, password_hash, name, is_verified, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 0, 1, ?, ?)""",
                    (user_id, email, password_hash, name, now, now),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                raise UserAlreadyExistsError(f"邮箱已被注册: {email}")
            finally:
                conn.close()

        logger.info(f"用户注册成功: {user_id} ({email})")
        return self.get_by_id(user_id)

    def authenticate(self, email: str, password: str) -> User:
        """验证用户凭证（OAuth2 标准流程）。

        Args:
            email: 用户邮箱
            password: 明文密码

        Returns:
            验证通过的 User 实例

        Raises:
            InvalidCredentialsError: 邮箱或密码错误
            UserSuspendedError: 用户已被禁用
        """
        user = self.get_by_email(email)
        if not user or not self.verify_password(password, user.password_hash):
            raise InvalidCredentialsError("邮箱或密码错误")

        if not user.is_active:
            raise UserSuspendedError("账户已被禁用")

        return user

    def get_by_id(self, user_id: str) -> Optional[User]:
        """根据用户 ID 获取用户。"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return _user_from_row(row) if row else None
        finally:
            conn.close()

    def get_by_email(self, email: str) -> Optional[User]:
        """根据邮箱获取用户。"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            return _user_from_row(row) if row else None
        finally:
            conn.close()

    def verify_email(self, user_id: str) -> User:
        """标记邮箱为已验证。"""
        user = self.get_by_id(user_id)
        if not user:
            raise UserNotFoundError(f"用户不存在: {user_id}")

        now = int(datetime.utcnow().timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "UPDATE users SET is_verified = 1, updated_at = ? WHERE user_id = ?",
                    (now, user_id),
                )
                conn.commit()
            finally:
                conn.close()

        logger.info(f"邮箱已验证: {user_id}")
        return self.get_by_id(user_id)

    def change_password(self, user_id: str, old_password: str, new_password: str) -> User:
        """修改密码。"""
        user = self.get_by_id(user_id)
        if not user:
            raise UserNotFoundError(f"用户不存在: {user_id}")
        if not self.verify_password(old_password, user.password_hash):
            raise InvalidCredentialsError("原密码错误")

        new_hash = self.hash_password(new_password)
        now = int(datetime.utcnow().timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?",
                    (new_hash, now, user_id),
                )
                conn.commit()
            finally:
                conn.close()

        logger.info(f"密码已修改: {user_id}")
        return self.get_by_id(user_id)

    def _update_password(self, user_id: str, new_hash: str) -> User:
        """直接更新密码哈希（用于密码重置，不验证旧密码）。"""
        now = int(datetime.utcnow().timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?",
                    (new_hash, now, user_id),
                )
                conn.commit()
            finally:
                conn.close()
        logger.info(f"密码已重置: {user_id}")
        return self.get_by_id(user_id)