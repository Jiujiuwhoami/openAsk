"""文档切分器：使用策略模式支持不同的切分策略。"""

from abc import ABC, abstractmethod
from typing import List

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from src.utils.logger import get_logger

logger = get_logger(__name__)


class DocumentSplitStrategy(ABC):
    """文档切分策略抽象基类。"""

    @abstractmethod
    def split(self, docs: List[Document]) -> List[Document]:
        """切分文档列表。

        Args:
            docs: 待切分的文档列表

        Returns:
            切分后的文档列表
        """
        pass


class NoSplitStrategy(DocumentSplitStrategy):
    """不切分策略：返回原始文档。

    适用于短文档（如 FAQ），保持文档完整性。
    """

    def split(self, docs: List[Document]) -> List[Document]:
        """不进行切分，直接返回原文档。"""
        logger.debug(f"使用 NoSplitStrategy，返回 {len(docs)} 个原始文档")
        return docs


class RecursiveSplitStrategy(DocumentSplitStrategy):
    """递归切分策略：按字符数切分文档。

    使用 RecursiveCharacterTextSplitter，智能识别段落边界，
    优先按段落、句子、单词顺序切分，保持语义完整性。

    Args:
        chunk_size: 每个 chunk 的最大字符数
        chunk_overlap: 相邻 chunk 之间的重叠字符数
    """

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
        )

    def split(self, docs: List[Document]) -> List[Document]:
        """递归切分文档。"""
        split_docs = []
        for doc in docs:
            chunks = self._splitter.split_documents([doc])
            split_docs.extend(chunks)
        logger.debug(
            f"使用 RecursiveSplitStrategy，{len(docs)} 个文档切分为 {len(split_docs)} 个 chunk"
        )
        return split_docs


class DocumentSplitter:
    """文档切分器：根据策略切分文档。

    使用策略模式，支持多种切分策略：
    - NoSplitStrategy：不切分，适用于短文档
    - RecursiveSplitStrategy：递归切分，适用于长文档

    Examples:
        >>> splitter = DocumentSplitter(RecursiveSplitStrategy(chunk_size=500))
        >>> docs = splitter.split(raw_docs)
    """

    def __init__(self, strategy: DocumentSplitStrategy):
        self._strategy = strategy

    def split(self, docs: List[Document]) -> List[Document]:
        """使用当前策略切分文档。"""
        return self._strategy.split(docs)

    def set_strategy(self, strategy: DocumentSplitStrategy) -> None:
        """切换切分策略。"""
        self._strategy = strategy
        logger.debug(f"切分策略已切换为: {strategy.__class__.__name__}")