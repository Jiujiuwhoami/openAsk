"""文档加载器扩展测试 — 工厂、各加载器、supports。"""

import os
import tempfile
from unittest.mock import Mock, patch, mock_open

import pytest

from src.domain.exceptions import KnowledgeBaseError
from src.services.document_loader import (
    DocumentLoaderFactory,
    MarkdownLoader,
    TextLoader,
    DocxLoader,
    HtmlLoader,
    PyPDFLoader,
    ImageLoader,
)


# ================================================================
# DocumentLoaderFactory
# ================================================================


class TestDocumentLoaderFactory:
    def test_get_loader_md(self):
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False, encoding="utf-8") as f:
            f.write("# 标题\n内容")
            path = f.name
        try:
            loader = DocumentLoaderFactory.get_loader(path)
            assert isinstance(loader, MarkdownLoader)
        finally:
            os.unlink(path)

    def test_get_loader_txt(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"text")
            path = f.name
        try:
            loader = DocumentLoaderFactory.get_loader(path)
            assert isinstance(loader, TextLoader)
        finally:
            os.unlink(path)

    def test_get_loader_unsupported(self):
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"data")
            path = f.name
        try:
            with pytest.raises(KnowledgeBaseError, match="不支持的文件格式"):
                DocumentLoaderFactory.get_loader(path)
        finally:
            os.unlink(path)

    def test_get_loader_not_found(self):
        with pytest.raises(KnowledgeBaseError, match="文件不存在"):
            DocumentLoaderFactory.get_loader("/nonexistent/path.md")

    def test_supports_true(self):
        assert DocumentLoaderFactory.supports("test.md") is True
        assert DocumentLoaderFactory.supports("test.pdf") is True
        assert DocumentLoaderFactory.supports("test.png") is True

    def test_supports_false(self):
        assert DocumentLoaderFactory.supports("test.xyz") is False
        assert DocumentLoaderFactory.supports("test") is False


# ================================================================
# MarkdownLoader
# ================================================================


class TestMarkdownLoader:
    def test_load_markdown(self):
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False, encoding="utf-8") as f:
            f.write("# 标题\n\n这是内容")
            path = f.name
        try:
            loader = MarkdownLoader(path)
            docs = loader.load()
            assert len(docs) == 1
            assert "# 标题\n\n这是内容" in docs[0].page_content
            assert docs[0].metadata["source"] == path
        finally:
            os.unlink(path)

    def test_load_markdown_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False, encoding="utf-8") as f:
            f.write("")
            path = f.name
        try:
            loader = MarkdownLoader(path)
            docs = loader.load()
            assert len(docs) == 1
        finally:
            os.unlink(path)

    def test_load_error(self):
        loader = MarkdownLoader("/nonexistent/file.md")
        with pytest.raises(KnowledgeBaseError, match="加载失败"):
            loader.load()


# ================================================================
# TextLoader
# ================================================================


class TestTextLoader:
    def test_load_text(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as f:
            f.write("纯文本内容")
            path = f.name
        try:
            loader = TextLoader(path)
            docs = loader.load()
            assert len(docs) == 1
            assert "纯文本内容" in docs[0].page_content
        finally:
            os.unlink(path)

    def test_load_text_error(self):
        loader = TextLoader("/nonexistent.txt")
        with pytest.raises(KnowledgeBaseError, match="加载失败"):
            loader.load()


# ================================================================
# DocxLoader
# ================================================================


class TestDocxLoader:
    def test_load_docx_not_installed(self):
        """python-docx 未安装时抛友好错误。"""
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            path = f.name
        try:
            with patch.dict('sys.modules', {'docx': None}):
                loader = DocxLoader(path)
                with pytest.raises(KnowledgeBaseError, match="python-docx"):
                    loader.load()
        finally:
            os.unlink(path)


# ================================================================
# HtmlLoader
# ================================================================


class TestHtmlLoader:
    def test_load_html_not_installed(self):
        """BeautifulSoup 未安装时抛友好错误。"""
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write("<html><body>内容</body></html>")
            path = f.name
        try:
            with patch.dict('sys.modules', {'bs4': None, 'BeautifulSoup': None}):
                loader = HtmlLoader(path)
                with pytest.raises(KnowledgeBaseError, match="beautifulsoup4"):
                    loader.load()
        finally:
            os.unlink(path)

    def test_load_html_success(self):
        with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False, encoding="utf-8") as f:
            f.write("<html><head><title>测试</title></head><body><p>页面内容</p></body></html>")
            path = f.name
        try:
            loader = HtmlLoader(path)
            docs = loader.load()
            assert len(docs) == 1
            assert "页面内容" in docs[0].page_content
        finally:
            os.unlink(path)


# ================================================================
# PyPDFLoader
# ================================================================


class TestPyPDFLoader:
    def test_load_pdf_error(self):
        """不存在的 PDF 文件抛出错误。"""
        loader = PyPDFLoader("/nonexistent.pdf")
        with pytest.raises(KnowledgeBaseError, match="加载失败"):
            loader.load()


# ================================================================
# ImageLoader
# ================================================================


class TestImageLoader:
    def test_load_with_injected_service(self):
        """使用注入的多模态服务。"""
        mock_service = Mock()
        mock_service.describe_image = Mock(return_value="图片描述文字")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"fake_png")
            path = f.name
        try:
            loader = ImageLoader(path, multimodal_service=mock_service)
            docs = loader.load()
            assert len(docs) == 1
            assert "图片描述文字" in docs[0].page_content
            mock_service.describe_image.assert_called_once_with(path)
        finally:
            os.unlink(path)

    def test_load_image_error(self):
        """图片不存在时抛出错误。"""
        loader = ImageLoader("/nonexistent.png")
        with pytest.raises(KnowledgeBaseError, match="图片识别失败"):
            loader.load()

    def test_get_service_uses_injected(self):
        service = Mock()
        loader = ImageLoader("test.png", multimodal_service=service)
        assert loader._get_service() is service

    def test_get_service_creates_new(self):
        with patch("src.infrastructure.multimodal_service.MultiModalServiceFactory.get_service") as mock_get:
            mock_get.return_value = Mock()
            loader = ImageLoader("test.png")
            svc = loader._get_service()
            assert svc is not None
            mock_get.assert_called_once()