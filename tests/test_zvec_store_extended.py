"""Zvec 向量存储扩展测试 — batch_search, list_paginated, delete_by_project, get, close, filter, async 变体。"""

import os
import tempfile

import numpy as np
import pytest

from src.domain.models import Document
from src.infrastructure.zvec_store import ZvecStore


@pytest.fixture
def temp_zvec_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "zvec_test")


@pytest.fixture
def store(temp_zvec_path):
    z = ZvecStore(data_path=temp_zvec_path, dimension=384)
    yield z
    z.close()


def _make_doc(doc_id="doc_1", content="内容", title="标题", project_id="default"):
    return Document(doc_id=doc_id, content=content, title=title, tags=["test"],
                    project_id=project_id)


def _make_vec():
    return np.random.rand(384).astype(np.float32)


# ================================================================
# 基础操作
# ================================================================


class TestGet:
    def test_get_existing(self, store):
        doc = _make_doc("get_1")
        store.insert(doc, _make_vec())
        found = store.get("get_1")
        assert found is not None
        assert found.doc_id == "get_1"
        assert found.title == "标题"

    def test_get_nonexistent(self, store):
        assert store.get("no_such_doc") is None

    def test_get_wrong_project(self, store):
        doc = _make_doc("get_2", project_id="proj_a")
        store.insert(doc, _make_vec(), project_id="proj_a")
        # 用不同 project_id 查不到
        assert store.get("get_2", project_id="proj_b") is None


class TestInsertAndCount:
    def test_insert_multiple(self, store):
        for i in range(3):
            store.insert(_make_doc(f"multi_{i}"), _make_vec())
        assert store.count() == 3

    def test_count_by_project(self, store):
        store.insert(_make_doc("a", project_id="proj_x"), _make_vec(), project_id="proj_x")
        store.insert(_make_doc("b", project_id="proj_x"), _make_vec(), project_id="proj_x")
        store.insert(_make_doc("c", project_id="proj_y"), _make_vec(), project_id="proj_y")
        assert store.count(project_id="proj_x") == 2
        assert store.count(project_id="proj_y") == 1


class TestBatchSearch:
    def test_batch_search_empty(self, store):
        result = store.batch_search([], top_k=5)
        assert result == []

    def test_batch_search_multiple(self, store):
        doc = _make_doc("batch_1")
        vec = _make_vec()
        store.insert(doc, vec)
        store.insert(_make_doc("batch_2"), vec)

        results = store.batch_search([vec, vec], top_k=5)
        assert len(results) == 2
        assert len(results[0]) >= 2
        assert len(results[1]) >= 2


class TestListPaginated:
    def test_list_paginated_empty(self, store):
        assert store.list_paginated(page=1, page_size=10) == []

    def test_list_paginated_single_page(self, store):
        for i in range(3):
            store.insert(_make_doc(f"list_{i}"), _make_vec())
        docs = store.list_paginated(page=1, page_size=10)
        assert len(docs) == 3

    def test_list_paginated_page_out_of_range(self, store):
        for i in range(3):
            store.insert(_make_doc(f"lop_{i}"), _make_vec())
        docs = store.list_paginated(page=5, page_size=10)  # 超出范围
        assert docs == []

    def test_list_paginated_respects_project(self, store):
        store.insert(_make_doc("d1", project_id="proj_a"), _make_vec(), project_id="proj_a")
        store.insert(_make_doc("d2", project_id="proj_b"), _make_vec(), project_id="proj_b")
        docs_a = store.list_paginated(page=1, page_size=10, project_id="proj_a")
        assert len(docs_a) == 1
        assert docs_a[0].doc_id == "d1"


class TestDeleteByProject:
    def test_delete_by_project(self, store):
        store.insert(_make_doc("a", project_id="proj_x"), _make_vec(), project_id="proj_x")
        store.insert(_make_doc("b", project_id="proj_x"), _make_vec(), project_id="proj_x")
        store.insert(_make_doc("c", project_id="proj_y"), _make_vec(), project_id="proj_y")
        store.delete_by_project_id("proj_x")
        # Zvec delete_by_filter 返回值可能为 None，验证实际效果
        assert store.count(project_id="proj_x") == 0
        assert store.count(project_id="proj_y") == 1

    def test_delete_by_project_default(self, store):
        store.insert(_make_doc("a"), _make_vec())
        store.insert(_make_doc("b"), _make_vec())
        store.delete_by_project_id("default")
        assert store.count() == 0


class TestDelete:
    def test_delete_nonexistent(self, store):
        result = store.delete("no_such_doc")
        assert result is False

    def test_delete_wrong_project(self, store):
        store.insert(_make_doc("del_1", project_id="proj_a"), _make_vec(), project_id="proj_a")
        result = store.delete("del_1", project_id="proj_b")
        assert result is False


class TestUpsert:
    def test_upsert_new(self, store):
        doc = _make_doc("upsert_1")
        store.upsert(doc, _make_vec())
        assert store.count() == 1

    def test_upsert_update(self, store):
        doc = _make_doc("upsert_2", content="原内容")
        store.insert(doc, _make_vec())
        doc2 = _make_doc("upsert_2", content="新内容")
        store.upsert(doc2, _make_vec())
        assert store.count() == 1
        found = store.get("upsert_2")
        assert found.content == "新内容"


class TestReorder:
    def test_search_returns_scored_results(self, store):
        """确认检索结果包含分数。"""
        doc = _make_doc("score_test")
        vec = _make_vec()  # 随机向量
        store.insert(doc, vec)
        results = store.search(vec, top_k=5)
        assert len(results) >= 1
        assert results[0].score is not None
        assert results[0].doc_id == "score_test"


# ================================================================
# 异步变体
# ================================================================


@pytest.mark.asyncio
class TestAsyncVariants:
    async def test_ainsert_and_acount(self, store):
        doc = _make_doc("async_1")
        vec = _make_vec()
        await store.ainsert(doc, vec)
        count = await store.acount()
        assert count >= 1

    async def test_asearch(self, store):
        doc = _make_doc("async_search")
        vec = _make_vec()
        await store.ainsert(doc, vec)
        results = await store.asearch(vec, top_k=5)
        assert len(results) >= 1
        assert results[0].doc_id == "async_search"

    async def test_aget(self, store):
        doc = _make_doc("async_get")
        await store.ainsert(doc, _make_vec())
        found = await store.aget("async_get")
        assert found is not None

    async def test_aget_not_found(self, store):
        found = await store.aget("async_nonexistent")
        assert found is None

    async def test_adelete(self, store):
        doc = _make_doc("async_del")
        await store.ainsert(doc, _make_vec())
        result = await store.adelete("async_del")
        assert result is True

    async def test_adelete_not_found(self, store):
        result = await store.adelete("async_no_such")
        assert result is False

    async def test_aupsert(self, store):
        doc = _make_doc("async_upsert")
        await store.aupsert(doc, _make_vec())
        count = await store.acount()
        assert count >= 1

    async def test_abatch_search(self, store):
        doc = _make_doc("abatch_1")
        vec = _make_vec()
        await store.ainsert(doc, vec)
        results = await store.abatch_search([vec], top_k=5)
        assert len(results) == 1
        assert len(results[0]) >= 1

    async def test_alist_paginated(self, store):
        for i in range(3):
            await store.ainsert(_make_doc(f"alist_{i}"), _make_vec())
        docs = await store.alist_paginated(page=1, page_size=10)
        assert len(docs) >= 3

    async def test_adelete_by_project(self, store):
        await store.ainsert(_make_doc("adp_1", project_id="proj_a"), _make_vec(), project_id="proj_a")
        await store.adelete_by_project_id("proj_a")
        # Zvec 返回值可能为 None，验证实际效果
        assert await store.acount(project_id="proj_a") == 0

    async def test_alist(self, store):
        await store.ainsert(_make_doc("alist2"), _make_vec())
        docs = await store.alist()
        assert len(docs) >= 1

    async def test_aclose(self, store):
        await store.aclose()
        # 关闭后应可安全再次关闭
        await store.aclose()