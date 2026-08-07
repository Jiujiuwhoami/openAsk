"""领域模型：Project（用户的项目/知识库空间）。"""

import time
from typing import Optional


class Project:
    """实体：项目，一个用户可创建多个项目，每个项目有独立 API Key 和知识库。"""

    def __init__(
        self,
        project_id: str,
        user_id: str,
        api_key: str,
        name: str,
        status: str = "active",
        llm_api_key: str = "",
        llm_api_base: str = "",
        llm_model: str = "",
        llm_timeout: int = 30,
        rate_limit_per_user: str = "60/minute",
        rate_limit_global: str = "1000/minute",
        system_prompt: str = "",
        language: str = "zh",
        created_at: Optional[int] = None,
        updated_at: Optional[int] = None,
    ):
        self._project_id = project_id
        self._user_id = user_id
        self._api_key = api_key
        self._name = name
        self._status = status
        self._llm_api_key = llm_api_key
        self._llm_api_base = llm_api_base
        self._llm_model = llm_model
        self._llm_timeout = llm_timeout
        self._rate_limit_per_user = rate_limit_per_user
        self._rate_limit_global = rate_limit_global
        self._system_prompt = system_prompt
        self._language = language
        now = int(time.time())
        self._created_at = created_at if created_at is not None else now
        self._updated_at = updated_at if updated_at is not None else now

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def user_id(self) -> str:
        return self._user_id

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
    def language(self) -> str:
        return self._language

    @property
    def created_at(self) -> int:
        return self._created_at

    @property
    def updated_at(self) -> int:
        return self._updated_at

    @property
    def is_active(self) -> bool:
        return self._status == "active"

    def __repr__(self) -> str:
        return f"Project(project_id={self._project_id}, name={self._name})"