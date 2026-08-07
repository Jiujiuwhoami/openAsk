"""用户服务单元测试 — 注册、登录、JWT、密码管理。"""

import os
import tempfile
import time
import pytest
from datetime import datetime, timedelta

from src.services.user_service import UserService
from src.domain.user import User
from src.domain.exceptions import (
    UserAlreadyExistsError,
    InvalidCredentialsError,
    UserSuspendedError,
    UserNotFoundError,
)


@pytest.fixture
def db_path():
    """临时 SQLite 数据库路径。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def service(db_path):
    """使用临时数据库的 UserService 实例。"""
    return UserService(db_path=db_path)


# ================================================================
# 注册
# ================================================================


class TestRegister:
    def test_register_success(self, service):
        """正常注册返回 User 实例。"""
        user = service.register("test@example.com", "password123", "测试用户")
        assert isinstance(user, User)
        assert user.email == "test@example.com"
        assert user.name == "测试用户"
        assert user.is_verified is False
        assert user.is_active is True
        assert user.user_id.startswith("user_")

    def test_register_duplicate_email(self, service):
        """重复邮箱抛出 UserAlreadyExistsError。"""
        service.register("dup@example.com", "password123")
        with pytest.raises(UserAlreadyExistsError):
            service.register("dup@example.com", "otherpass456")

    def test_register_minimal(self, service):
        """只传邮箱和密码也能注册成功。"""
        user = service.register("minimal@example.com", "password123")
        assert user.name == ""


# ================================================================
# 密码管理
# ================================================================


class TestPassword:
    def test_hash_and_verify(self, service):
        """密码哈希后可以验证。"""
        h = service.hash_password("my_secret_pwd")
        assert h != "my_secret_pwd"
        assert service.verify_password("my_secret_pwd", h) is True
        assert service.verify_password("wrong_password", h) is False

    def test_different_hash_each_time(self, service):
        """每次哈希结果不同（盐值随机）。"""
        h1 = service.hash_password("same_password")
        h2 = service.hash_password("same_password")
        assert h1 != h2


# ================================================================
# JWT 管理
# ================================================================


class TestJWT:
    def test_create_and_decode_token(self, service):
        """创建并解码 JWT token。"""
        token = service.create_access_token("user_123")
        payload = service.decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "user_123"
        assert payload["type"] == "access"

    def test_decode_invalid_token(self, service):
        """无效 token 返回 None。"""
        assert service.decode_access_token("invalid.token.here") is None

    def test_token_expiration(self, service):
        """过期 token 返回 None。"""
        token = service.create_access_token("user_123", expires_delta=timedelta(days=-1))
        assert service.decode_access_token(token) is None


# ================================================================
# 认证
# ================================================================


class TestAuthenticate:
    def test_authenticate_success(self, service):
        """正确凭证返回 User。"""
        service.register("auth@example.com", "password123")
        user = service.authenticate("auth@example.com", "password123")
        assert isinstance(user, User)
        assert user.email == "auth@example.com"

    def test_authenticate_wrong_password(self, service):
        """错误密码抛出 InvalidCredentialsError。"""
        service.register("wrong@example.com", "correctpwd")
        with pytest.raises(InvalidCredentialsError):
            service.authenticate("wrong@example.com", "wrongpwd")

    def test_authenticate_nonexistent_email(self, service):
        """不存在的邮箱抛出 InvalidCredentialsError。"""
        with pytest.raises(InvalidCredentialsError):
            service.authenticate("nobody@example.com", "password123")

    def test_authenticate_suspended_user(self, service):
        """已禁用用户抛出 UserSuspendedError。"""
        service.register("suspend@example.com", "password123")
        # 直接修改数据库标记为禁用
        conn = service._get_connection()
        conn.execute("UPDATE users SET is_active = 0 WHERE email = ?", ("suspend@example.com",))
        conn.commit()
        conn.close()

        with pytest.raises(UserSuspendedError):
            service.authenticate("suspend@example.com", "password123")


# ================================================================
# 用户管理
# ================================================================


class TestUserManagement:
    def test_get_by_id(self, service):
        """通过 ID 获取用户。"""
        user = service.register("getbyid@example.com", "password123")
        found = service.get_by_id(user.user_id)
        assert found is not None
        assert found.email == "getbyid@example.com"

    def test_get_by_id_not_found(self, service):
        """不存在的 ID 返回 None。"""
        assert service.get_by_id("nonexistent_user") is None

    def test_get_by_email(self, service):
        """通过邮箱获取用户。"""
        user = service.register("getbyemail@example.com", "password123")
        found = service.get_by_email("getbyemail@example.com")
        assert found is not None
        assert found.user_id == user.user_id

    def test_verify_email(self, service):
        """验证邮箱。"""
        user = service.register("verify@example.com", "password123")
        assert user.is_verified is False
        verified = service.verify_email(user.user_id)
        assert verified.is_verified is True

    def test_change_password(self, service):
        """修改密码后可用新密码登录。"""
        service.register("changepwd@example.com", "oldpassword")
        service.change_password(
            service.get_by_email("changepwd@example.com").user_id,
            "oldpassword",
            "newpassword",
        )
        # 旧密码失效
        with pytest.raises(InvalidCredentialsError):
            service.authenticate("changepwd@example.com", "oldpassword")
        # 新密码可用
        user = service.authenticate("changepwd@example.com", "newpassword")
        assert user is not None

    def test_change_password_wrong_old(self, service):
        """旧密码错误抛出异常。"""
        service.register("wrongold@example.com", "correctpwd")
        with pytest.raises(InvalidCredentialsError):
            service.change_password(
                service.get_by_email("wrongold@example.com").user_id,
                "wrongold",
                "newpwd",
            )

    def test_update_password(self, service):
        """直接更新密码哈希（用于重置）。"""
        service.register("resetpwd@example.com", "oldpassword")
        user = service.get_by_email("resetpwd@example.com")
        new_hash = service.hash_password("resetnewpwd")
        service._update_password(user.user_id, new_hash)
        # 新密码可用
        auth_user = service.authenticate("resetpwd@example.com", "resetnewpwd")
        assert auth_user is not None