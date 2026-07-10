"""重排序服务抽象接口。"""

from abc import ABC, abstractmethod
from typing import List

from src.domain.models import SearchResult


class Reranker(ABC):
    """重排序服务接口。

    负责对向量检索结果进行精排，提升最终回答质量。
    经典流程：向量检索召回 top-100 → 重排序精排 → 返回 top-5

    Examples:
        >>> reranker = BGEM3Reranker()
        >>> results = await reranker.rerank("查询", [SearchResult(...), ...])
    """

    @abstractmethod
    async def rerank(
        self,
        query: str,
        documents: List[SearchResult],
        top_k: int = 5,
    ) -> List[SearchResult]:
        """对文档列表进行重排序。

        Args:
            query: 用户查询文本
            documents: 待重排序的文档列表（来自向量检索）
            top_k: 返回前多少个结果

        Returns:
            按相关性重新排序后的文档列表
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """关闭资源。"""
        pass

    @property
    @abstractmethod
    def is_enabled(self) -> bool:
        """是否启用重排序。"""
        pass
