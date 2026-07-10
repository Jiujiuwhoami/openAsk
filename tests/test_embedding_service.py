"""Sentence-BERT 嵌入服务测试。"""

import numpy as np
import pytest

from src.infrastructure.embedding_service import SentenceBertEmbeddingService


@pytest.fixture(scope="module")
def embedding_service():
    """模块级 fixture，只加载一次模型。"""
    svc = SentenceBertEmbeddingService()
    yield svc


class TestSentenceBertEmbeddingService:
    """嵌入服务测试。"""

    def test_dimension(self, embedding_service):
        """向量维度应为 384。"""
        assert embedding_service.dimension() == 384

    @pytest.mark.asyncio
    async def test_encode_returns_correct_shape(self, embedding_service):
        """编码输出应为 (384,) 形状的一维向量。"""
        vec = await embedding_service.encode("测试文本")
        assert vec.shape == (384,)
        assert vec.dtype == np.float32

    @pytest.mark.asyncio
    async def test_encode_same_text_consistent(self, embedding_service):
        """相同文本应输出一致的向量。"""
        vec1 = await embedding_service.encode("相同文本")
        vec2 = await embedding_service.encode("相同文本")
        assert np.allclose(vec1, vec2)

    @pytest.mark.asyncio
    async def test_encode_different_text_different(self, embedding_service):
        """不同文本应输出不同的向量。"""
        vec1 = await embedding_service.encode("苹果")
        vec2 = await embedding_service.encode("香蕉")
        assert not np.allclose(vec1, vec2)

    @pytest.mark.asyncio
    async def test_encode_batch(self, embedding_service):
        """批量编码应返回正确形状的数组。"""
        texts = ["测试一", "测试二", "测试三"]
        batch = await embedding_service.encode_batch(texts)
        assert batch.shape == (3, 384)
        assert batch.dtype == np.float32

    @pytest.mark.asyncio
    async def test_encode_normalized(self, embedding_service):
        """输出向量应已归一化（L2 范数接近 1）。"""
        vec = await embedding_service.encode("归一化测试")
        norm = np.linalg.norm(vec)
        assert abs(norm - 1.0) < 0.01
