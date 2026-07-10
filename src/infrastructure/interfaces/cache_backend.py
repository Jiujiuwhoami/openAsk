"""缓存后端抽象接口。"""

from abc import ABC, abstractmethod
from typing import Optional

import numpy as np


class CacheBackend(ABC):
    """缓存后端接口。"""

    @abstractmethod
    def get(self, key: np.ndarray) -> Optional[str]:
        """根据向量键查找缓存。"""
        pass

    @abstractmethod
    def set(self, key: np.ndarray, value: str) -> None:
        """写入缓存。"""
        pass