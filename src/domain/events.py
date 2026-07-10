"""领域事件：观察者模式。"""

import datetime
from abc import ABC, abstractmethod

from src.domain.models import Document


class DomainEvent(ABC):
    """领域事件基类。"""

    @property
    @abstractmethod
    def occurred_on(self) -> datetime.datetime:
        pass


class DocumentAddedEvent(DomainEvent):
    """文档添加事件。"""

    def __init__(self, document: Document):
        self._document = document
        self._occurred_on = datetime.datetime.now()

    @property
    def document(self) -> Document:
        return self._document

    @property
    def occurred_on(self) -> datetime.datetime:
        return self._occurred_on


class DocumentUpdatedEvent(DomainEvent):
    """文档更新事件。"""

    def __init__(self, document: Document):
        self._document = document
        self._occurred_on = datetime.datetime.now()

    @property
    def document(self) -> Document:
        return self._document

    @property
    def occurred_on(self) -> datetime.datetime:
        return self._occurred_on


class DocumentDeletedEvent(DomainEvent):
    """文档删除事件。"""

    def __init__(self, doc_id: str):
        self._doc_id = doc_id
        self._occurred_on = datetime.datetime.now()

    @property
    def doc_id(self) -> str:
        return self._doc_id

    @property
    def occurred_on(self) -> datetime.datetime:
        return self._occurred_on