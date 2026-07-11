"""API 端点测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import router
from src.domain.models import SearchResult


class MockRetriever:
    """Mock Retriever 用于测试。"""

    def __init__(self):
        self._cache_hit = False

    def _build_result(self, query: str, top_k: int):
        """构建检索结果。"""
        if query == "cache_hit_query":
            self._cache_hit = True
            return MockRetrievalResult(
                answer="缓存的回答",
                sources=[
                    SearchResult(
                        doc_id="doc1",
                        score=0.95,
                        content="这是缓存文档内容",
                        title="缓存文档",
                    )
                ],
                cache_hit=True,
                llm_used=False,
            )

        return MockRetrievalResult(
            answer="基于文档生成的回答",
            sources=[
                SearchResult(
                    doc_id="doc1",
                    score=0.95,
                    content="这是相关文档内容",
                    title="相关文档",
                ),
                SearchResult(
                    doc_id="doc2",
                    score=0.85,
                    content="另一篇相关文档内容",
                    title="文档2",
                ),
            ],
            cache_hit=False,
            llm_used=True,
        )

    async def retrieve(self, query: str, top_k: int = 5):
        """模拟检索（异步）。"""
        return self._build_result(query, top_k)

    async def retrieve_stream(self, query: str, top_k: int = 5):
        """模拟流式检索（异步生成器）。"""
        result = self._build_result(query, top_k)
        sources_data = [
            {
                "doc_id": s.doc_id,
                "title": s.title,
                "content": s.content,
                "score": round(s.score, 4),
            }
            for s in result.sources
        ]
        yield {"event": "sources", "data": sources_data}
        yield {"event": "cache_hit", "data": result.cache_hit}
        for ch in result.answer:
            yield {"event": "answer_delta", "data": ch}
        yield {"event": "done", "data": {"reranked": False}}

    async def close(self):
        """模拟关闭（异步）。"""
        pass


class MockRetrievalResult:
    """Mock 检索结果。"""

    def __init__(self, answer, sources, cache_hit, llm_used):
        self.answer = answer
        self.sources = sources
        self.cache_hit = cache_hit
        self.llm_used = llm_used


class MockKnowledgeService:
    """Mock KnowledgeService 用于测试。"""

    def __init__(self):
        self._documents = {}
        self._next_id = 1

    async def create_document_from_text(self, title, content, tags=None, source=None):
        """模拟从文本创建文档（异步）。"""
        doc_id = f"doc{self._next_id}"
        self._next_id += 1
        doc = MockDocument(
            doc_id=doc_id,
            title=title,
            content=content,
            tags=tags or [],
            source=source,
        )
        self._documents[doc_id] = doc
        return doc

    async def load_and_store_document(self, file_path=None):
        """模拟加载并存储文档（异步）。"""
        doc_id = f"doc{self._next_id}"
        self._next_id += 1
        doc = MockDocument(
            doc_id=doc_id,
            title="测试文档",
            content="测试内容",
            tags=[],
            source=None,
        )
        self._documents[doc_id] = doc
        return doc

    async def get_by_id(self, doc_id):
        """模拟根据 ID 获取文档（异步）。"""
        return self._documents.get(doc_id)

    async def search(self, query: str, top_k: int = 10):
        """模拟搜索（异步）。"""
        if query in self._documents:
            return [self._documents[query]]
        return []

    async def batch_search(self, queries: list, top_k: int = 10):
        """模拟批量搜索（异步）。"""
        results = []
        for query in queries:
            if query in self._documents:
                results.append([self._documents[query]])
            else:
                results.append([])
        return results

    async def delete_document(self, doc_id: str):
        """模拟删除文档（异步）。"""
        if doc_id in self._documents:
            del self._documents[doc_id]
            return True
        return False

    async def count_documents(self):
        """模拟统计文档数量（异步）。"""
        return len(self._documents)

    async def list_documents(self, page=1, page_size=10):
        """模拟分页列出文档（异步）。"""
        all_docs = list(self._documents.values())
        start = (page - 1) * page_size
        end = start + page_size
        return all_docs[start:end]

    async def update_document(self, doc_id, title=None, content=None, tags=None, source=None):
        """模拟更新文档（异步）。"""
        if doc_id not in self._documents:
            from src.domain.exceptions import DocumentNotFoundError
            raise DocumentNotFoundError(f"文档不存在: {doc_id}")
        
        doc = self._documents[doc_id]
        if title is not None:
            doc.title = title
        if content is not None:
            doc.content = content
        if tags is not None:
            doc.tags = tags
        if source is not None:
            doc.source = source
        doc.updated_at = 1234567891
        return doc

    async def close(self):
        """模拟关闭（异步）。"""
        pass


class MockDocument:
    """Mock 文档。"""

    def __init__(self, doc_id, title, content, tags, source):
        self.doc_id = doc_id
        self.title = title
        self.content = content
        self.tags = tags or []
        self.source = source
        self.created_at = 1234567890
        self.updated_at = 1234567890

    def update(self, **kwargs):
        """模拟更新。"""
        for key, value in kwargs.items():
            setattr(self, key, value)


class MockVectorStore:
    """Mock VectorStore 用于测试。"""
    def count(self):
        return 0
    async def acount(self):
        return 0


class MockEmbeddingService:
    """Mock EmbeddingService 用于测试。"""
    def dimension(self):
        return 768


class MockLLMClient:
    """Mock LLMClient 用于测试。"""
    def __init__(self):
        self._api_key = "test_key"

    @property
    def is_configured(self) -> bool:
        """模拟 API 密钥是否已配置。"""
        return bool(self._api_key)

    async def generate_answer(self, query: str, context: list) -> str:
        """模拟生成回答。"""
        return "Mock LLM 回答"

    async def stream_answer(self, query: str, context: list):
        """模拟流式生成回答。"""
        for ch in "Mock LLM 回答":
            yield ch

    async def close(self):
        """模拟关闭。"""
        pass


class MockCacheBackend:
    """Mock CacheBackend 用于测试。"""
    pass


@pytest.fixture
def client():
    """创建测试客户端，使用 Mock 组件，完全绕过真实 lifespan。"""
    from src.utils.limiter import limiter
    test_app = FastAPI(
        title="OpenAsk Test",
        version="1.0.0",
    )

    test_app.state.retriever = MockRetriever()
    test_app.state.knowledge_service = MockKnowledgeService()
    test_app.state.vector_store = MockVectorStore()
    test_app.state.embedding_service = MockEmbeddingService()
    test_app.state.llm_client = MockLLMClient()
    test_app.state.cache_backend = MockCacheBackend()
    test_app.state.limiter = limiter

    test_app.include_router(router)

    with TestClient(test_app) as client:
        yield client


class TestHealthEndpoint:
    """健康检查端点测试。"""

    def test_health_check(self, client):
        """测试健康检查返回正常。"""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"
        assert "timestamp" in data
        assert data["zvec_status"] == "healthy"
        assert data["embedding_status"] == "healthy"
        assert data["llm_status"] == "healthy"
        assert data["cache_status"] == "healthy"
        assert data["document_count"] == 0


class TestChatEndpoint:
    """聊天端点测试。"""

    def test_chat_success(self, client):
        """测试正常聊天请求。"""
        response = client.post("/api/chat", json={"query": "测试问题", "top_k": 2})
        assert response.status_code == 200
        data = response.json()
        assert data["answer"] == "基于文档生成的回答"
        assert len(data["sources"]) == 2
        assert not data["cache_hit"]
        assert data["llm_used"]

    def test_chat_cache_hit(self, client):
        """测试缓存命中。"""
        response = client.post(
            "/api/chat", json={"query": "cache_hit_query", "top_k": 2}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["answer"] == "缓存的回答"
        assert data["cache_hit"]
        assert not data["llm_used"]

    def test_chat_empty_query(self, client):
        """测试空查询。"""
        response = client.post("/api/chat", json={"query": "", "top_k": 2})
        assert response.status_code == 422

    def test_chat_missing_query(self, client):
        """测试缺少查询参数。"""
        response = client.post("/api/chat", json={"top_k": 2})
        assert response.status_code == 422


class TestKnowledgeEndpoints:
    """知识库端点测试。"""

    def test_create_document(self, client):
        """测试创建文档。"""
        response = client.post(
            "/api/knowledge",
            json={
                "title": "测试标题",
                "content": "测试内容",
                "tags": ["tag1", "tag2"],
                "source": "manual",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "测试标题"
        assert data["content"] == "测试内容"
        assert data["tags"] == ["tag1", "tag2"]

    def test_create_document_missing_fields(self, client):
        """测试缺少必填字段。"""
        response = client.post("/api/knowledge", json={"title": "测试标题"})
        assert response.status_code == 422

    def test_get_document(self, client):
        """测试获取文档。"""
        create_response = client.post(
            "/api/knowledge",
            json={"title": "测试标题", "content": "测试内容"},
        )
        doc_id = create_response.json()["doc_id"]

        get_response = client.get(f"/api/knowledge/{doc_id}")
        assert get_response.status_code == 200
        data = get_response.json()
        assert data["doc_id"] == doc_id
        assert data["title"] == "测试标题"

    def test_get_document_not_found(self, client):
        """测试获取不存在的文档。"""
        response = client.get("/api/knowledge/nonexistent")
        assert response.status_code == 404

    def test_delete_document(self, client):
        """测试删除文档。"""
        create_response = client.post(
            "/api/knowledge",
            json={"title": "测试标题", "content": "测试内容"},
        )
        doc_id = create_response.json()["doc_id"]

        delete_response = client.delete(f"/api/knowledge/{doc_id}")
        assert delete_response.status_code == 200
        data = delete_response.json()
        assert data["success"] == True

    def test_delete_document_not_found(self, client):
        """测试删除不存在的文档。"""
        response = client.delete("/api/knowledge/nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] == False

    def test_search(self, client):
        """测试搜索接口。"""
        response = client.post("/api/search", json={"query": "测试", "top_k": 5})
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_documents(self, client):
        """测试列出文档接口。"""
        for i in range(3):
            client.post(
                "/api/knowledge",
                json={"title": f"文档{i}", "content": f"内容{i}"},
            )

        response = client.get("/api/knowledge?page=1&page_size=10")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) == 3
        assert data["total"] == 3

    def test_update_document(self, client):
        """测试更新文档。"""
        create_response = client.post(
            "/api/knowledge",
            json={"title": "原始标题", "content": "原始内容"},
        )
        doc_id = create_response.json()["doc_id"]

        update_response = client.put(
            f"/api/knowledge/{doc_id}",
            json={"title": "更新后的标题", "tags": ["updated"]},
        )
        assert update_response.status_code == 200
        data = update_response.json()
        assert data["doc_id"] == doc_id
        assert data["title"] == "更新后的标题"
        assert data["content"] == "原始内容"
        assert data["tags"] == ["updated"]

    def test_update_document_not_found(self, client):
        """测试更新不存在的文档。"""
        response = client.put(
            "/api/knowledge/nonexistent",
            json={"title": "新标题"},
        )
        assert response.status_code == 404