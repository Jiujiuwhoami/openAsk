"""文档加载器测试。"""

import os
import pytest

from src.services.document_loader import DocumentLoaderFactory
from src.domain.exceptions import KnowledgeBaseError

FAQ_DIR = "data/documents/faq"


def test_supports_markdown():
    """测试支持 markdown 文件。"""
    assert DocumentLoaderFactory.supports("test.md") is True


def test_supports_txt():
    """测试支持 txt 文件。"""
    assert DocumentLoaderFactory.supports("test.txt") is True


def test_supports_pdf():
    """测试支持 pdf 文件。"""
    assert DocumentLoaderFactory.supports("test.pdf") is True


def test_supports_docx():
    """测试支持 docx 文件。"""
    assert DocumentLoaderFactory.supports("test.docx") is True


def test_supports_html():
    """测试支持 html 文件。"""
    assert DocumentLoaderFactory.supports("test.html") is True


def test_not_supports_unknown():
    """测试不支持未知格式。"""
    assert DocumentLoaderFactory.supports("test.unknown") is False


def test_load_markdown_document():
    """测试加载 markdown 文档。"""
    if not os.path.exists(FAQ_DIR):
        pytest.skip("FAQ 目录不存在")

    faq_files = [f for f in os.listdir(FAQ_DIR) if f.endswith(".md")]
    assert len(faq_files) > 0, "FAQ 目录为空"

    file_path = os.path.join(FAQ_DIR, faq_files[0])
    loader = DocumentLoaderFactory.get_loader(file_path)
    docs = loader.load()

    assert len(docs) == 1
    assert docs[0].page_content.strip() != ""


def test_load_nonexistent_file():
    """测试加载不存在的文件。"""
    with pytest.raises(KnowledgeBaseError):
        DocumentLoaderFactory.get_loader("nonexistent.md")