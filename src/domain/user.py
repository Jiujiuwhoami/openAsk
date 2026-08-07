"""领域模型：User（平台用户）。"""

import time
from typing import Optional


class User:
    """实体：平台用户，由 email 唯一标识。"""

    def __init__(
        self,
        user_id: str,
        email: str,
        password_hash: str,
        name: str = "",
        is_verified: bool = False,
        is_active: bool = True,
        created_at: Optional[int] = None,
        updated_at: Optional[int] = None,
    ):
        self._user_id = user_id
        self._email = email
        self._password_hash = password_hash
        self._name = name
        self._is_verified = is_verified
        self._is_active = is_active
        now = int(time.time())
        self._created_at = created_at if created_at is not None else now
        self._updated_at = updated_at if updated_at is not None else now

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def email(self) -> str:
        return self._email

    @property
    def password_hash(self) -> str:
        return self._password_hash

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_verified(self) -> bool:
        return self._is_verified

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def created_at(self) -> int:
        return self._created_at

    @property
    def updated_at(self) -> int:
        return self._updated_at

    def verify(self) -> None:
        """标记邮箱为已验证。"""
        self._is_verified = True
        self._updated_at = int(time.time())

    def __repr__(self) -> str:
        return f"User(user_id={self._user_id}, email={self._email})"