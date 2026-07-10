"""BGE-Reranker 重排序服务实现。

BGE-Reranker 是开源重排序模型，基于 BGE-M3 架构，
在 MTEB 排行榜上表现出色，适合作为 RAG 精排阶段。

流程：向量检索召回 top-100 → BGE-Reranker 精排 → 返回 top-k

线程安全：使用 threading.RLock 保护模型推理
异步接口：使用 asyncio.to_thread 避免阻塞事件循环
"""

import asyncio
import threading
from typing import List, Optional

from src.domain.models import SearchResult
from src.infrastructure.interfaces.reranker import Reranker
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


class BGEM3Reranker(Reranker):
    """基于 BGE-M3 的重排序服务。

    使用 sentence-transformers 加载 BGE-Reranker 模型，
    对向量检索结果进行精排，显著提升 RAG 系统的检索准确率。

    性能特点：
    - 召回阶段使用向量检索，速度快（O(log n)）
    - 精排阶段使用 BGE-Reranker，准确率高但较慢
    - 通过控制 recall_top_k 和 rerank_top_k 平衡速度和准确率

    Examples:
        >>> reranker = BGEM3Reranker()
        >>> results = await reranker.rerank("如何退款？", [doc1, doc2, ...], top_k=5)
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
        enabled: Optional[bool] = None,
        recall_top_k: Optional[int] = None,
        rerank_top_k: Optional[int] = None,
    ):
        self._model_name = model_name or settings.reranker.model_name
        self._device = device or settings.reranker.device
        self._enabled = enabled if enabled is not None else settings.reranker.enabled
        self._recall_top_k = recall_top_k or settings.reranker.recall_top_k
        self._rerank_top_k = rerank_top_k or settings.reranker.rerank_top_k
        self._model = None
        self._lock = threading.RLock()

        if self._enabled:
            with self._lock:
                self._load_model()

    def _load_model(self) -> None:
        """加载 BGE-Reranker 模型。"""
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name, device=self._device)
            logger.info(
                f"BGE-Reranker 模型加载完成: {self._model_name} "
                f"(设备: {self._device}, 启用: {self._enabled})"
            )
        except ImportError:
            logger.warning(
                "sentence-transformers 未安装，重排序功能将不可用。"
                "请运行: pip install sentence-transformers"
            )
            self._enabled = False
        except Exception as e:
            logger.error(f"BGE-Reranker 模型加载失败: {e}", exc_info=True)
            self._enabled = False

    def _rerank_sync(
        self,
        query: str,
        documents: List[SearchResult],
        top_k: int,
    ) -> List[SearchResult]:
        """同步重排序（内部用）。"""
        if not documents:
            return []

        pairs = [[query, doc.content] for doc in documents]

        with self._lock:
            scores = self._model.predict(pairs)

        scored_docs = list(zip(documents, scores))
        scored_docs.sort(key=lambda x: x[1], reverse=True)

        result = []
        for doc, score in scored_docs[:top_k]:
            result.append(
                SearchResult(
                    doc_id=doc.doc_id,
                    score=float(score),
                    content=doc.content,
                    title=doc.title,
                    tags=doc.tags,
                )
            )

        logger.debug(f"重排序完成: {len(documents)} → {len(result)} (top-{top_k})")
        return result

    async def rerank(
        self,
        query: str,
        documents: List[SearchResult],
        top_k: int = 5,
    ) -> List[SearchResult]:
        """对文档列表进行重排序（异步）。

        使用 asyncio.to_thread 在独立线程中执行，不阻塞事件循环。

        Args:
            query: 用户查询文本
            documents: 待重排序的文档列表
            top_k: 返回前多少个结果

        Returns:
            按相关性重新排序后的文档列表
        """
        if not self._enabled or not documents:
            return documents[:top_k]

        try:
            return await asyncio.to_thread(
                self._rerank_sync, query, documents, top_k
            )
        except Exception as e:
            logger.error(f"重排序失败，降级为不重排序: {e}", exc_info=True)
            return documents[:top_k]

    def close(self) -> None:
        """关闭资源。"""
        with self._lock:
            if self._model is not None:
                del self._model
                self._model = None
                logger.info("BGE-Reranker 模型已释放")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def recall_top_k(self) -> int:
        return self._recall_top_k

    @property
    def rerank_top_k(self) -> int:
        return self._rerank_top_k


class NoOpReranker(Reranker):
    """空实现重排序器：不做任何重排序，直接返回原结果。

    用于重排序功能未启用时的降级方案。
    """

    def __init__(self):
        self._enabled = False

    async def rerank(
        self,
        query: str,
        documents: List[SearchResult],
        top_k: int = 5,
    ) -> List[SearchResult]:
        return documents[:top_k]

    def close(self) -> None:
        pass

    @property
    def is_enabled(self) -> bool:
        return self._enabled


def create_reranker() -> Reranker:
    """创建重排序器实例。"""
    if settings.reranker.enabled:
        return BGEM3Reranker()
    return NoOpReranker()
