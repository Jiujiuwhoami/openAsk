"""领域模型：实体与值对象。"""

import time
from typing import List, Optional


class Document:
    """实体：知识库文档，由 doc_id 标识身份。"""

    def __init__(
        self,
        doc_id: str,
        content: str,
        title: str = "",
        tags: Optional[List[str]] = None,
        source: Optional[str] = None,
        created_at: Optional[int] = None,
        updated_at: Optional[int] = None,
    ):
        self._doc_id = doc_id
        self._content = content
        self._title = title
        self._tags = tags or []
        self._source = source
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
    ):
        self._doc_id = doc_id
        self._score = score
        self._content = content
        self._title = title
        self._tags = tags or []

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