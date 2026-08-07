"""领域模型：Conversation（多轮对话会话）。"""

import time
from typing import List, Optional


class Conversation:
    """实体：多轮对话会话，包含一组消息，按时间线组织。"""

    def __init__(
        self,
        conversation_id: str,
        project_id: str,
        title: str = "",
        status: str = "active",
        message_count: int = 0,
        created_at: Optional[int] = None,
        updated_at: Optional[int] = None,
    ):
        self._conversation_id = conversation_id
        self._project_id = project_id
        self._title = title
        self._status = status
        self._message_count = message_count
        now = int(time.time())
        self._created_at = created_at if created_at is not None else now
        self._updated_at = updated_at if updated_at is not None else now

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def title(self) -> str:
        return self._title

    @property
    def status(self) -> str:
        return self._status

    @property
    def message_count(self) -> int:
        return self._message_count

    @property
    def created_at(self) -> int:
        return self._created_at

    @property
    def updated_at(self) -> int:
        return self._updated_at

    def __repr__(self) -> str:
        return (
            f"Conversation(id={self._conversation_id}, "
            f"title={self._title}, messages={self._message_count})"
        )


class Message:
    """值对象：对话中的一条消息。"""

    def __init__(
        self,
        id: int,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[str] = None,
        created_at: Optional[int] = None,
    ):
        self._id = id
        self._conversation_id = conversation_id
        self._role = role
        self._content = content
        self._metadata = metadata
        self._created_at = created_at if created_at is not None else int(time.time())

    @property
    def id(self) -> int:
        return self._id

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def role(self) -> str:
        return self._role

    @property
    def content(self) -> str:
        return self._content

    @property
    def metadata(self) -> Optional[str]:
        return self._metadata

    @property
    def created_at(self) -> int:
        return self._created_at

    def to_dict(self) -> dict:
        """转为 LLM 协议格式（OpenAI messages 兼容）。"""
        return {"role": self._role, "content": self._content}

    def __repr__(self) -> str:
        return f"Message(id={self._id}, role={self._role}, content={self._content[:30]}...)"