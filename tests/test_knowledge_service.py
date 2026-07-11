"""知识库服务测试。"""

import os
import pytest
from unittest.mock import Mock, AsyncMock

from src.services.knowledge_service import KnowledgeService
from src.domain.models import Document
from src.domain.exceptions import KnowledgeBaseError

FAQ_DIR = "data/documents/faq"


@pytest.fixture
def mock_services():
    """创建 mock 的 vector_store 和 embedding_service。"""
    mock_vector_store = Mock()
    mock_vector_store.acount = AsyncMock(return_value=0)
    mock_vector_store.ainsert = AsyncMock(return_value=None)
    mock_vector_store.adelete = AsyncMock(return_value=False)
    mock_vector_store.asearch = AsyncMock(return_value=[])
    mock_vector_store.aget = AsyncMock(return_value=None)
    mock_vector_store.alist_paginated = AsyncMock(return_value=[])

    mock_embedding_service = Mock()
    mock_embedding_service.encode = AsyncMock(return_value=[0.1] * 384)
    mock_embedding_service.encode_batch = AsyncMock(return_value=[[0.1] * 384])

    return mock_vector_store, mock_embedding_service


@pytest.mark.asyncio
async def test_count_documents(mock_services):
    """测试文档计数。"""
    mock_vector_store, mock_embedding_service = mock_services
    mock_vector_store.acount.return_value = 5

    svc = KnowledgeService(mock_vector_store, mock_embedding_service)
    count = await svc.count_documents()

    assert count == 5
    mock_vector_store.acount.assert_called_once()


@pytest.mark.asyncio
async def test_load_document(mock_services):
    """测试加载文档。"""
    if not os.path.exists(FAQ_DIR):
        pytest.skip("FAQ 目录不存在")

    mock_vector_store, mock_embedding_service = mock_services
    svc = KnowledgeService(mock_vector_store, mock_embedding_service)

    faq_files = [f for f in os.listdir(FAQ_DIR) if f.endswith(".md")]
    assert len(faq_files) > 0

    file_path = os.path.join(FAQ_DIR, faq_files[0])
    doc = await svc.load_document(file_path)

    assert isinstance(doc, Document)
    assert doc.doc_id is not None
    assert doc.content.strip() != ""


@pytest.mark.asyncio
async def test_load_and_store_document(mock_services):
    """测试加载并存储文档。"""
    if not os.path.exists(FAQ_DIR):
        pytest.skip("FAQ 目录不存在")

    mock_vector_store, mock_embedding_service = mock_services
    svc = KnowledgeService(mock_vector_store, mock_embedding_service)

    faq_files = [f for f in os.listdir(FAQ_DIR) if f.endswith(".md")]
    file_path = os.path.join(FAQ_DIR, faq_files[0])

    doc = await svc.load_and_store_document(file_path)

    assert isinstance(doc, Document)
    mock_embedding_service.encode.assert_called_once()
    mock_vector_store.ainsert.assert_called_once()


@pytest.mark.asyncio
async def test_delete_document(mock_services):
    """测试删除文档。"""
    mock_vector_store, mock_embedding_service = mock_services
    mock_vector_store.adelete.return_value = True

    svc = KnowledgeService(mock_vector_store, mock_embedding_service)
    result = await svc.delete_document("test-doc-id")

    assert result is True
    mock_vector_store.adelete.assert_called_once_with("test-doc-id")


@pytest.mark.asyncio
async def test_search(mock_services):
    """测试文档检索。"""
    from src.domain.models import SearchResult

    mock_vector_store, mock_embedding_service = mock_services
    mock_vector_store.asearch.return_value = [
        SearchResult(doc_id="doc1", score=0.95, content="测试内容", title="测试标题")
    ]

    svc = KnowledgeService(mock_vector_store, mock_embedding_service)
    results = await svc.search("测试查询", top_k=5)

    assert len(results) == 1
    assert isinstance(results[0], Document)
    mock_embedding_service.encode.assert_called_once_with("测试查询")
    mock_vector_store.asearch.assert_called_once()


@pytest.mark.asyncio
async def test_load_nonexistent_directory(mock_services):
    """测试加载不存在的目录。"""
    mock_vector_store, mock_embedding_service = mock_services
    svc = KnowledgeService(mock_vector_store, mock_embedding_service)

    with pytest.raises(KnowledgeBaseError):
        await svc.load_directory("nonexistent_dir")