"""嵌入服务抽象接口。"""

from abc import ABC, abstractmethod
from typing import List

import numpy as np


class EmbeddingService(ABC):
    """嵌入服务接口。"""

    @abstractmethod
    async def encode(self, text: str) -> np.ndarray:
        """将文本编码为向量。"""
        pass

    @abstractmethod
    async def encode_batch(self, texts: List[str]) -> np.ndarray:
        """批量编码文本。"""
        pass

    @abstractmethod
    def dimension(self) -> int:
        """返回向量维度。"""
        pass

    @property
    @abstractmethod
    def batch_size(self) -> int:
        """返回批大小。"""
        pass
