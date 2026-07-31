"""领域模型：实体与值对象。"""

import time
from typing import List, Optional

# 默认租户 ID：用于兼容单租户遗留数据
DEFAULT_TENANT_ID = "default"


class Tenant:
    """实体：多租户，由 tenant_id 标识身份。

    每个租户拥有独立的知识库数据、LLM 配置和限流策略。
    存储在后端 SQLite（小团队优先），后续可扩展至 PostgreSQL。
    """

    def __init__(
        self,
        tenant_id: str,
        api_key: str,
        name: str,
        status: str = "active",
        knowledge_path: str = "",
        llm_api_key: str = "",
        llm_api_base: str = "",
        llm_model: str = "",
        llm_timeout: int = 30,
        rate_limit_per_user: str = "60/minute",
        rate_limit_global: str = "1000/minute",
        system_prompt: str = "",
        created_at: Optional[int] = None,
        updated_at: Optional[int] = None,
    ):
        self._tenant_id = tenant_id
        self._api_key = api_key
        self._name = name
        self._status = status
        self._knowledge_path = knowledge_path
        self._llm_api_key = llm_api_key
        self._llm_api_base = llm_api_base
        self._llm_model = llm_model
        self._llm_timeout = llm_timeout
        self._rate_limit_per_user = rate_limit_per_user
        self._rate_limit_global = rate_limit_global
        self._system_prompt = system_prompt
        now = int(time.time())
        self._created_at = created_at if created_at is not None else now
        self._updated_at = updated_at if updated_at is not None else now

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def name(self) -> str:
        return self._name

    @property
    def status(self) -> str:
        return self._status

    @property
    def knowledge_path(self) -> str:
        return self._knowledge_path

    @property
    def llm_api_key(self) -> str:
        return self._llm_api_key

    @property
    def llm_api_base(self) -> str:
        return self._llm_api_base

    @property
    def llm_model(self) -> str:
        return self._llm_model

    @property
    def llm_timeout(self) -> int:
        return self._llm_timeout

    @property
    def rate_limit_per_user(self) -> str:
        return self._rate_limit_per_user

    @property
    def rate_limit_global(self) -> str:
        return self._rate_limit_global

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def created_at(self) -> int:
        return self._created_at

    @property
    def updated_at(self) -> int:
        return self._updated_at

    @property
    def is_active(self) -> bool:
        """租户是否处于活跃状态。"""
        return self._status == "active"

    def update(
        self,
        name: str = "",
        status: str = "",
        llm_api_key: str = "",
        llm_api_base: str = "",
        llm_model: str = "",
        llm_timeout: int = 0,
        rate_limit_per_user: str = "",
        rate_limit_global: str = "",
        system_prompt: str = "",
    ) -> None:
        """更新租户属性。"""
        if name:
            self._name = name
        if status:
            self._status = status
        if llm_api_key:
            self._llm_api_key = llm_api_key
        if llm_api_base:
            self._llm_api_base = llm_api_base
        if llm_model:
            self._llm_model = llm_model
        if llm_timeout > 0:
            self._llm_timeout = llm_timeout
        if rate_limit_per_user:
            self._rate_limit_per_user = rate_limit_per_user
        if rate_limit_global:
            self._rate_limit_global = rate_limit_global
        if system_prompt != "":
            self._system_prompt = system_prompt
        self._updated_at = int(time.time())

    def rotate_api_key(self) -> str:
        """轮换 API Key（租户侧生成新 key，实际由 TenantService 持久化）。"""
        import secrets

        self._api_key = "sk_" + secrets.token_hex(16)
        self._updated_at = int(time.time())
        return self._api_key


class Document:
    """实体：知识库文档，由 doc_id 标识身份。"""

    def __init__(
        self,
        doc_id: str,
        content: str,
        title: str = "",
        tags: Optional[List[str]] = None,
        source: Optional[str] = None,
        tenant_id: str = DEFAULT_TENANT_ID,
        created_at: Optional[int] = None,
        updated_at: Optional[int] = None,
    ):
        self._doc_id = doc_id
        self._content = content
        self._title = title
        self._tags = tags or []
        self._source = source
        self._tenant_id = tenant_id
        now = int(time.time())
        self._created_at = created_at if created_at is not None else now
        self._updated_at = updated_at if updated_at is not None else now

    @property
    def doc_id(self) -> str:
        return self._doc_id

    @property
    def content(self) -> str:
        return self._content

    @property
    def title(self) -> str:
        return self._title

    @property
    def tags(self) -> List[str]:
        return list(self._tags)

    @property
    def source(self) -> Optional[str]:
        return self._source

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def created_at(self) -> int:
        return self._created_at

    @property
    def updated_at(self) -> int:
        return self._updated_at

    def update(
        self,
        content: str = "",
        title: str = "",
        tags: Optional[List[str]] = None,
        source: Optional[str] = None,
    ) -> None:
        if content:
            self._content = content
        if title:
            self._title = title
        if tags is not None:
            self._tags = tags
        if source is not None:
            self._source = source
        self._updated_at = int(time.time())


class SearchResult:
    """值对象：检索结果，不可变。"""

    def __init__(
        self,
        doc_id: str,
        score: float,
        content: str,
        title: str = "",
        tags: Optional[List[str]] = None,
        tenant_id: str = DEFAULT_TENANT_ID,
    ):
        self._doc_id = doc_id
        self._score = score
        self._content = content
        self._title = title
        self._tags = tags or []
        self._tenant_id = tenant_id

    @property
    def doc_id(self) -> str:
        return self._doc_id

    @property
    def score(self) -> float:
        return self._score

    @property
    def content(self) -> str:
        return self._content

    @property
    def title(self) -> str:
        return self._title

    @property
    def tags(self) -> List[str]:
        return list(self._tags)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SearchResult):
            return False
        return (
            self._doc_id == other._doc_id
            and self._score == other._score
            and self._content == other._content
        )

    def __hash__(self) -> int:
        return hash((self._doc_id, self._score, self._content))

    @property
    def tenant_id(self) -> str:
        return self._tenant_id