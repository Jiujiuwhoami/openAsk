"""KnowledgeService 扩展测试 — create/update/batch/close。"""

import pytest
from unittest.mock import Mock, AsyncMock, MagicMock

from src.services.knowledge_service import KnowledgeService
from src.domain.models import Document, SearchResult
from src.domain.exceptions import KnowledgeBaseError


@pytest.fixture
def mock_vector_store():
    vs = Mock()
    vs.acount = AsyncMock(return_value=0)
    vs.ainsert = AsyncMock(return_value=None)
    vs.adelete = AsyncMock(return_value=False)
    vs.asearch = AsyncMock(return_value=[])
    vs.aget = AsyncMock(return_value=None)
    vs.alist_paginated = AsyncMock(return_value=[])
    vs.aupsert = AsyncMock(return_value=None)
    vs.asearch_by_hash = AsyncMock(return_value=None)
    vs.abatch_search = AsyncMock(return_value=[])
    vs.aclose = AsyncMock(return_value=None)
    return vs


@pytest.fixture
def mock_embedding():
    emb = Mock()
    emb.encode = AsyncMock(return_value=[0.1] * 384)
    emb.encode_batch = AsyncMock(return_value=[[0.1] * 384, [0.2] * 384])
    return emb


@pytest.fixture
def svc(mock_vector_store, mock_embedding):
    return KnowledgeService(mock_vector_store, mock_embedding)


# ================================================================
# create_document_from_text
# ================================================================


class TestCreateDocumentFromText:
    @pytest.mark.asyncio
    async def test_create_success(self, svc, mock_vector_store):
        doc = await svc.create_document_from_text("标题", "内容", tags=["tag1"])
        assert doc.title == "标题"
        assert doc.content == "内容"
        assert doc.tags == ["tag1"]
        mock_vector_store.ainsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_with_project(self, svc, mock_vector_store):
        doc = await svc.create_document_from_text("标题", "内容", project_id="proj_1")
        assert doc.project_id == "proj_1"
        mock_vector_store.ainsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_duplicate_raises(self, svc, mock_vector_store):
        # 模拟重复内容
        mock_doc = Document(doc_id="existing", content="内容", title="已存在", project_id="proj_1")
        mock_vector_store.asearch_by_hash = AsyncMock(return_value=mock_doc)

        with pytest.raises(KnowledgeBaseError, match="内容已存在"):
            await svc.create_document_from_text("标题", "内容", project_id="proj_1")

    @pytest.mark.asyncio
    async def test_create_skip_duplicate(self, svc, mock_vector_store):
        doc = await svc.create_document_from_text("标题", "内容", skip_duplicate_check=True, project_id="proj_1")
        assert doc is not None
        mock_vector_store.asearch_by_hash.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_with_source(self, svc):
        doc = await svc.create_document_from_text("标题", "内容", source="manual")
        assert doc.source == "manual"


# ================================================================
# update_document
# ================================================================


class TestUpdateDocument:
    @pytest.mark.asyncio
    async def test_update_existing(self, svc, mock_vector_store):
        existing = Document(doc_id="doc_1", content="旧内容", title="旧标题", tags=["a"], source="src", project_id="proj_1")
        mock_vector_store.aget = AsyncMock(return_value=existing)

        updated = await svc.update_document("doc_1", title="新标题", project_id="proj_1")
        assert updated.title == "新标题"
        assert updated.content == "旧内容"  # 未传则保留
        mock_vector_store.aupsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, svc, mock_vector_store):
        mock_vector_store.aget = AsyncMock(return_value=None)
        with pytest.raises(KnowledgeBaseError, match="文档不存在"):
            await svc.update_document("doc_404", project_id="proj_1")

    @pytest.mark.asyncio
    async def test_update_all_fields(self, svc, mock_vector_store):
        existing = Document(doc_id="doc_1", content="旧", title="旧", tags=["a"], source="src", project_id="proj_1")
        mock_vector_store.aget = AsyncMock(return_value=existing)

        updated = await svc.update_document("doc_1", title="新标题", content="新内容", tags=["b"], source="new_src", project_id="proj_1")
        assert updated.title == "新标题"
        assert updated.content == "新内容"
        assert updated.tags == ["b"]
        assert updated.source == "new_src"


# ================================================================
# batch_delete_documents
# ================================================================


class TestBatchDelete:
    @pytest.mark.asyncio
    async def test_batch_delete_all_success(self, svc, mock_vector_store):
        mock_vector_store.adelete = AsyncMock(return_value=True)
        count = await svc.batch_delete_documents(["doc_1", "doc_2"], project_id="proj_1")
        assert count == 2
        assert mock_vector_store.adelete.call_count == 2

    @pytest.mark.asyncio
    async def test_batch_delete_partial(self, svc, mock_vector_store):
        mock_vector_store.adelete = AsyncMock(side_effect=[True, False])
        count = await svc.batch_delete_documents(["doc_1", "doc_2"], project_id="proj_1")
        assert count == 1

    @pytest.mark.asyncio
    async def test_batch_delete_exception_handled(self, svc, mock_vector_store):
        mock_vector_store.adelete = AsyncMock(side_effect=Exception("db error"))
        count = await svc.batch_delete_documents(["doc_1"], project_id="proj_1")
        assert count == 0  # 异常被捕获，不中断


# ================================================================
# list_documents
# ================================================================


class TestListDocuments:
    @pytest.mark.asyncio
    async def test_list_documents(self, svc, mock_vector_store):
        mock_vector_store.alist_paginated = AsyncMock(return_value=[
            Document(doc_id="1", content="内容", title="标题", project_id="proj_1"),
        ])
        docs = await svc.list_documents(page=1, page_size=10, project_id="proj_1")
        assert len(docs) == 1
        mock_vector_store.alist_paginated.assert_called_once_with(page=1, page_size=10, project_id="proj_1")


# ================================================================
# batch_search
# ================================================================


class TestBatchSearch:
    @pytest.mark.asyncio
    async def test_batch_search_uses_abatch(self, svc, mock_vector_store):
        mock_vector_store.abatch_search = AsyncMock(return_value=[
            [SearchResult(doc_id="d1", score=0.9, content="c1", title="t1")],
            [SearchResult(doc_id="d2", score=0.8, content="c2", title="t2")],
        ])
        results = await svc.batch_search(["q1", "q2"], top_k=5, project_id="proj_1")
        assert len(results) == 2
        assert results[0][0].doc_id == "d1"
        mock_vector_store.abatch_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_search_empty(self, svc, mock_vector_store):
        results = await svc.batch_search([], project_id="proj_1")
        assert results == []


# ================================================================
# close
# ================================================================


class TestClose:
    @pytest.mark.asyncio
    async def test_close(self, svc, mock_vector_store):
        await svc.close()
        mock_vector_store.aclose.assert_called_once()


# ================================================================
# get_by_id
# ================================================================


class TestGetById:
    @pytest.mark.asyncio
    async def test_get_by_id_found(self, svc, mock_vector_store):
        mock_doc = Document(doc_id="doc_1", content="内容", title="标题", project_id="proj_1")
        mock_vector_store.aget = AsyncMock(return_value=mock_doc)
        doc = await svc.get_by_id("doc_1", project_id="proj_1")
        assert doc is not None
        assert doc.doc_id == "doc_1"

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, svc, mock_vector_store):
        mock_vector_store.aget = AsyncMock(return_value=None)
        doc = await svc.get_by_id("doc_404", project_id="proj_1")
        assert doc is None