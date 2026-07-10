"""Sentence-BERT 嵌入服务实现。"""

import asyncio
import threading
from typing import List, Optional

import numpy as np
from sentence_transformers import SentenceTransformer

from src.domain.exceptions import EmbeddingError
from src.infrastructure.interfaces.embedding_service import EmbeddingService
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SentenceBertEmbeddingService(EmbeddingService):
    """基于 Sentence-BERT 的嵌入服务。

    线程安全：内部使用 threading.RLock 保护模型编码操作。
    异步接口：使用 asyncio.to_thread 在独立线程中执行 CPU 密集型编码，
    不阻塞 FastAPI 事件循环。
    批量支持：自动按 batch_size 分批处理，避免内存溢出。
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        batch_size: Optional[int] = None,
        device: Optional[str] = None,
        normalize_embeddings: Optional[bool] = None,
    ):
        self._model_name = model_name or settings.embedding.model_name
        self._batch_size = batch_size or settings.embedding.batch_size
        self._device = device or settings.embedding.device
        self._normalize_embeddings = (
            normalize_embeddings
            if normalize_embeddings is not None
            else settings.embedding.normalize_embeddings
        )
        self._model: SentenceTransformer = None  # type: ignore[assignment]
        self._dim: int = 0
        self._lock = threading.RLock()
        with self._lock:
            self._load_model()

    def _load_model(self) -> None:
        try:
            self._model = SentenceTransformer(self._model_name, device=self._device)
            self._dim = self._model.get_embedding_dimension()
            logger.info(
                f"嵌入模型加载完成: {self._model_name} "
                f"(维度: {self._dim}, 批大小: {self._batch_size}, 设备: {self._device})"
            )
        except Exception as e:
            logger.error(f"嵌入模型加载失败: {e}", exc_info=True)
            raise EmbeddingError(f"Failed to load embedding model: {e}")

    def _encode_sync(self, text: str) -> np.ndarray:
        """同步编码单条文本（内部用）。"""
        with self._lock:
            embedding = self._model.encode(
                text,
                normalize_embeddings=self._normalize_embeddings,
                show_progress_bar=False,
            )
        return embedding.astype(np.float32)

    def _encode_batch_sync(self, texts: List[str]) -> np.ndarray:
        """同步批量编码（内部用）。"""
        if not texts:
            return np.array([], dtype=np.float32).reshape(0, self._dim)

        all_embeddings = []
        with self._lock:
            for i in range(0, len(texts), self._batch_size):
                batch = texts[i : i + self._batch_size]
                embeddings = self._model.encode(
                    batch,
                    normalize_embeddings=self._normalize_embeddings,
                    show_progress_bar=False,
                    batch_size=self._batch_size,
                )
                all_embeddings.append(embeddings.astype(np.float32))
                logger.debug(f"批次 {i // self._batch_size + 1} 编码完成: {len(batch)} 条")

        result = np.vstack(all_embeddings)
        logger.info(f"批量编码完成: 共 {len(texts)} 条文本")
        return result

    async def encode(self, text: str) -> np.ndarray:
        """异步将文本编码为向量。

        使用 asyncio.to_thread 在独立线程中执行，不阻塞事件循环。
        """
        try:
            return await asyncio.to_thread(self._encode_sync, text)
        except Exception as e:
            logger.error(f"文本编码失败: {e}", exc_info=True)
            raise EmbeddingError(f"Failed to encode text: {e}")

    async def encode_batch(self, texts: List[str]) -> np.ndarray:
        """异步批量编码文本。

        自动按 batch_size 分批处理，避免内存溢出。
        """
        try:
            return await asyncio.to_thread(self._encode_batch_sync, texts)
        except Exception as e:
            logger.error(f"批量编码失败: {e}", exc_info=True)
            raise EmbeddingError(f"Failed to encode batch: {e}")

    def dimension(self) -> int:
        return self._dim

    @property
    def batch_size(self) -> int:
        return self._batch_size
