"""ZvecStore content_hash 去重功能测试（内存哈希映射）。"""

import hashlib
import os
import tempfile

import numpy as np
import pytest

from src.domain.models import DEFAULT_PROJECT_ID, Document
from src.infrastructure.zvec_store import ZvecStore


@pytest.fixture
def store():
    """创建临时 ZvecStore 实例。"""
    tmpdir = tempfile.mkdtemp(prefix="zvec_hash_test_")
    s = ZvecStore(data_path=os.path.join(tmpdir, "collection"), dimension=8)
    yield s
    s.close()


def _make_doc(content: str, title: str = "测试文档", project_id: str = "proj_a"):
    return Document(
        doc_id=f"doc_{abs(hash(content))}",
        content=content,
        title=title,
        tags=["test"],
        source="unit-test",
        project_id=project_id,
    )


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _vec(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.random(8, dtype=np.float32)
    return v / np.linalg.norm(v)


class TestSearchByHash:
    def test_search_by_hash_found(self, store):
        """插入后能按 content_hash 找到文档。"""
        content = "这是用于去重测试的内容"
        doc = _make_doc(content)
        h = _hash(content)
        store.insert(doc, _vec(1), project_id="proj_a", content_hash=h)
        found = store.search_by_hash(h, project_id="proj_a")
        assert found is not None
        assert found.doc_id == doc.doc_id
        assert found.content == content

    def test_search_by_hash_not_found(self, store):
        """不存在的 content_hash 返回 None。"""
        assert store.search_by_hash("nonexistent_hash", project_id="proj_a") is None

    def test_search_by_hash_project_isolation(self, store):
        """跨项目查找返回 None（租户隔离）。"""
        content = "项目隔离测试内容"
        h = _hash(content)
        store.insert(_make_doc(content, project_id="proj_a"), _vec(1), project_id="proj_a", content_hash=h)
        assert store.search_by_hash(h, project_id="proj_b") is None
        assert store.search_by_hash(h, project_id="proj_a") is not None

    def test_search_by_hash_async(self, store):
        """异步版本行为一致。"""
        import asyncio
        content = "异步去重测试"
        h = _hash(content)
        store.insert(_make_doc(content, project_id="proj_a"), _vec(2), project_id="proj_a", content_hash=h)
        found = asyncio.run(store.asearch_by_hash(h, project_id="proj_a"))
        assert found is not None
        assert found.content == content

    def test_search_by_hash_empty_project(self, store):
        """默认项目下也能工作。"""
        content = "默认项目测试"
        h = _hash(content)
        store.insert(_make_doc(content, project_id=DEFAULT_PROJECT_ID), _vec(3), project_id=DEFAULT_PROJECT_ID, content_hash=h)
        found = store.search_by_hash(h, project_id=DEFAULT_PROJECT_ID)
        assert found is not None

    def test_delete_clears_hash(self, store):
        """删除文档后相应哈希映射也应清除。"""
        content = "删除映射测试"
        h = _hash(content)
        doc = _make_doc(content, project_id="proj_a")
        store.insert(doc, _vec(1), project_id="proj_a", content_hash=h)
        assert store.search_by_hash(h, project_id="proj_a") is not None
        store.delete(doc.doc_id, project_id="proj_a")
        assert store.search_by_hash(h, project_id="proj_a") is None