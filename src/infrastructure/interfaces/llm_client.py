"""LLM 客户端抽象接口。"""

from abc import ABC, abstractmethod
from typing import AsyncGenerator, Dict, List, Union


StreamChunk = Dict[str, Union[str, int]]


class LLMClient(ABC):
    """LLM 客户端接口。

    所有 LLM 提供商(SenseNova、OpenAI、通义千问等)都必须实现此接口,
    以便在 Retriever 等上层服务中无缝替换。
    """

    @abstractmethod
    async def generate_answer(self, query: str, context: List[str]) -> str:
        """根据查询和上下文生成回答。

        Args:
            query: 用户查询文本
            context: 检索到的上下文片段列表

        Returns:
            生成的回答文本
        """
        pass

    @abstractmethod
    async def stream_answer(
        self, query: str, context: List[str]
    ) -> AsyncGenerator[StreamChunk, None]:
        """流式生成回答，逐 token 产出文本增量。

        Args:
            query: 用户查询文本
            context: 检索到的上下文片段列表

        Yields:
            StreamChunk: 包含 type 和 content 的字典。
                type 为 "reasoning" 时 content 为推理链文本，
                type 为 "content" 时 content 为回答文本。
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """关闭 LLM 客户端，释放资源。"""
        pass

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """返回 LLM 客户端是否已配置(例如 API Key 是否有效)。"""
        pass