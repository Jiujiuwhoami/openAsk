"""多模态服务抽象接口。"""

from abc import ABC, abstractmethod
from typing import Optional

import numpy as np


class MultiModalService(ABC):
    """多模态服务抽象基类。

    定义图片理解和嵌入的通用接口，支持文本描述、向量嵌入等操作。
    不同的云端服务（如 OpenAI、SenseNova、阿里云等）实现此接口。

    Examples:
        >>> class OpenAIMultiModalService(MultiModalService):
        ...     def describe_image(self, image_path: str) -> str:
        ...         # 调用 OpenAI API
        ...         pass
        ...     def encode_image(self, image_path: str) -> np.ndarray:
        ...         # 获取图片嵌入向量
        ...         pass
    """

    @abstractmethod
    def describe_image(self, image_path: str, prompt: Optional[str] = None) -> str:
        """描述图片内容。

        Args:
            image_path: 图片文件路径
            prompt: 可选的提示词，用于引导描述方向

        Returns:
            图片的文本描述

        Raises:
            MultiModalError: 服务调用失败
        """
        pass

    @abstractmethod
    def encode_image(self, image_path: str) -> np.ndarray:
        """将图片编码为向量。

        Args:
            image_path: 图片文件路径

        Returns:
            图片的嵌入向量（与文本向量同一维度）

        Raises:
            MultiModalError: 服务调用失败
        """
        pass

    @abstractmethod
    def chat_with_image(
        self, image_path: str, query: str
    ) -> str:
        """基于图片回答问题。

        Args:
            image_path: 图片文件路径
            query: 用户问题

        Returns:
            基于图片内容的回答

        Raises:
            MultiModalError: 服务调用失败
        """
        pass