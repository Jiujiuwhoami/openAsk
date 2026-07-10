"""文档切分器测试。"""

import pytest
from langchain_core.documents import Document

from src.services.document_splitter import (
    DocumentSplitter,
    NoSplitStrategy,
    RecursiveSplitStrategy,
)


def test_no_split_strategy():
    """测试不切分策略。"""
    docs = [
        Document(page_content="短文档内容"),
        Document(page_content="另一个短文档"),
    ]
    splitter = DocumentSplitter(NoSplitStrategy())
    result = splitter.split(docs)

    assert len(result) == 2
    assert result[0].page_content == "短文档内容"
    assert result[1].page_content == "另一个短文档"


def test_recursive_split_strategy_short():
    """测试递归切分策略处理短文档。"""
    docs = [Document(page_content="短文档")]
    splitter = DocumentSplitter(RecursiveSplitStrategy(chunk_size=500))
    result = splitter.split(docs)

    assert len(result) == 1
    assert result[0].page_content == "短文档"


def test_recursive_split_strategy_long():
    """测试递归切分策略处理长文档。"""
    long_text = "这是一个很长的文档。" * 100
    docs = [Document(page_content=long_text)]
    splitter = DocumentSplitter(RecursiveSplitStrategy(chunk_size=50, chunk_overlap=10))
    result = splitter.split(docs)

    assert len(result) > 1
    for doc in result:
        assert len(doc.page_content) <= 50


def test_switch_strategy():
    """测试切换切分策略。"""
    docs = [Document(page_content="测试文档")]
    splitter = DocumentSplitter(NoSplitStrategy())
    result1 = splitter.split(docs)
    assert len(result1) == 1

    splitter.set_strategy(RecursiveSplitStrategy(chunk_size=5, chunk_overlap=1))
    result2 = splitter.split(docs)
    assert len(result2) >= 1