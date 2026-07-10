"""Zvec 向量存储测试。"""

import os
import tempfile

import numpy as np
import pytest

from src.domain.models import Document
from src.infrastructure.zvec_store import ZvecStore


@pytest.fixture
def temp_zvec_path():
    """临时 Zvec 数据目录，测试后自动清理。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "zvec_test")


@pytest.fixture
def zvec_store(temp_zvec_path):
    store = ZvecStore(data_path=temp_zvec_path, dimension=384)
    yield store
    store.close()


class TestZvecStore:
    """Zvec 向量存储测试。"""

    def test_insert_and_count(self, zvec_store):
        """插入后 count 应正确。"""
        doc = Document("test_001", "测试内容", title="测试标题", tags=["test"])
        vec = np.random.rand(384).astype(np.float32)
        zvec_store.insert(doc, vec)
        assert zvec_store.count() == 1

    def test_search_returns_results(self, zvec_store):
        """检索应返回相关结果。"""
        doc = Document("test_002", "苹果是一种水果", title="水果", tags=["food"])
        vec = np.random.rand(384).astype(np.float32)
        zvec_store.insert(doc, vec)

        results = zvec_store.search(vec, top_k=5)
        assert len(results) >= 1
        assert results[0].doc_id == "test_002"

    def test_delete_reduces_count(self, zvec_store):
        """删除后 count 应减少。"""
        doc = Document("test_003", "待删除内容", title="待删除")
        vec = np.random.rand(384).astype(np.float32)
        zvec_store.insert(doc, vec)
        assert zvec_store.count() == 1
        zvec_store.delete("test_003")
        assert zvec_store.count() == 0

    def test_upsert_idempotent(self, zvec_store):
        """重复 upsert 不应报错。"""
        doc = Document("test_004", "内容", title="标题")
        vec = np.random.rand(384).astype(np.float32)
        zvec_store.upsert(doc, vec)
        zvec_store.upsert(doc, vec)  # 第二次不应报错
        assert zvec_store.count() == 1

    def test_search_with_filter(self, zvec_store):
        """条件过滤应生效。"""
        doc1 = Document("test_005a", "内容A", title="A", tags=["active"])
        doc2 = Document("test_005b", "内容B", title="B", tags=["inactive"])
        vec = np.random.rand(384).astype(np.float32)
        zvec_store.insert(doc1, vec)
        zvec_store.insert(doc2, vec)

        results = zvec_store.search(vec, top_k=5, filter_expr="doc_id = 'test_005a'")
        assert len(results) >= 1
        assert all(r.doc_id == "test_005a" for r in results)