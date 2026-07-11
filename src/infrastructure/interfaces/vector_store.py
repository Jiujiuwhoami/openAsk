"""向量存储抽象接口。"""

from abc import ABC, abstractmethod
from typing import List, Optional

import numpy as np

from src.domain.models import Document, SearchResult


class VectorStore(ABC):
    """向量存储接口。"""

    @abstractmethod
    def insert(self, doc: Document, dense_vector: np.ndarray) -> None:
        """插入文档及其向量。"""
        pass

    @abstractmethod
    def upsert(self, doc: Document, dense_vector: np.ndarray) -> None:
        """更新或插入文档。"""
        pass

    @abstractmethod
    def delete(self, doc_id: str) -> bool:
        """删除文档。返回是否成功。"""
        pass

    @abstractmethod
    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 5,
        filter_expr: Optional[str] = None,
    ) -> List[SearchResult]:
        """向量相似度检索。"""
        pass

    @abstractmethod
    def batch_search(
        self,
        query_vectors: List[np.ndarray],
        top_k: int = 5,
        filter_expr: Optional[str] = None,
    ) -> List[List[SearchResult]]:
        """批量向量相似度检索。"""
        pass

    @abstractmethod
    def count(self) -> int:
        """返回存储的文档总数。"""
        pass

    @abstractmethod
    def close(self) -> None:
        """关闭资源。"""
        pass