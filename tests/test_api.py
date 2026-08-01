"""API 端点测试（适配多租户架构）。

多租户改造后变更：
  - 所有业务接口需携带 X-API-Key（/api/health 除外）
  - 知识库 CRUD 操作透传 tenant_id
  - Retriever 由 RetrieverFactory 按租户分发
  - Mock 组件需兼容 tenant_id 参数
"""

import os
import sys
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import router
from src.api.schemas import ChatRequest
from src.domain.models import SearchResult
from src.utils.config import settings


# ================================================================
# 常量
# ================================================================

TEST_API_KEY = "sk_test_api_key_for_unit"
TEST_TENANT_ID = "test_tenant_unit"


# ================================================================
# Mock 领域模型
# ================================================================

class MockRetrievalResult:
    """Mock 检索结果。"""

    def __init__(self, answer, sources, cache_hit, llm_used):
        self.answer = answer
        self.sources = sources
        self.cache_hit = cache_hit
        self.llm_used = llm_used


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
        for key, value in kwargs.items():
            setattr(self, key, value)


class MockRetriever:
    """Mock Retriever，兼容 tenant_id 参数。

    需要 _llm_client 属性以支持 /api/health 的健康检查。
    """

    def __init__(self):
        self._llm_client = MockLLMClient()

    def _build_result(self, query, top_k=5):
        if query == "cache_hit_query":
            return MockRetrievalResult(
                answer="缓存的回答",
                sources=[
                    SearchResult(
                        doc_id="doc1", score=0.95,
                        content="这是缓存文档内容", title="缓存文档",
                    )
                ],
                cache_hit=True,
                llm_used=False,
            )

        return MockRetrievalResult(
            answer="基于文档生成的回答",
            sources=[
                SearchResult(
                    doc_id="doc1", score=0.95,
                    content="这是相关文档内容", title="相关文档",
                ),
                SearchResult(
                    doc_id="doc2", score=0.85,
                    content="另一篇相关文档内容", title="文档2",
                ),
            ],
            cache_hit=False,
            llm_used=True,
        )

    async def retrieve(self, query, top_k=5, tenant_id=None, cache_backend=None, system_prompt=None):
        return self._build_result(query, top_k)

    async def retrieve_stream(self, query, top_k=5, tenant_id=None, cache_backend=None, system_prompt=None):
        result = self._build_result(query, top_k)
        sources_data = [
            {"doc_id": s.doc_id, "title": s.title, "content": s.content, "score": round(s.score, 4)}
            for s in result.sources
        ]
        yield {"event": "sources", "data": sources_data}
        yield {"event": "cache_hit", "data": result.cache_hit}
        for ch in result.answer:
            yield {"event": "answer_delta", "data": ch}
        yield {"event": "done", "data": {"reranked": False}}

    async def close(self):
        pass


class MockFactory:
    """Mock RetrieverFactory，测试中返回共享的 MockRetriever。"""

    def __init__(self, retriever):
        self._retriever = retriever
        self._closed = False

    def get_retriever_for_tenant(self, tenant_id, tenant=None):
        if self._closed:
            raise RuntimeError("Factory 已关闭")
        return self._retriever

    async def close(self):
        self._closed = True


class MockKnowledgeService:
    """Mock KnowledgeService，兼容 tenant_id 透传。"""

    def __init__(self):
        self._documents = {}
        self._next_id = 1

    async def create_document_from_text(self, title, content, tenant_id=None, tags=None, source=None):
        doc_id = f"doc{self._next_id}"
        self._next_id += 1
        doc = MockDocument(doc_id=doc_id, title=title, content=content, tags=tags or [], source=source)
        self._documents[doc_id] = doc
        return doc

    async def load_and_store_document(self, file_path=None, tenant_id=None):
        doc_id = f"doc{self._next_id}"
        self._next_id += 1
        doc = MockDocument(doc_id=doc_id, title="测试文档", content="测试内容", tags=[], source=None)
        self._documents[doc_id] = doc
        return doc

    async def get_by_id(self, doc_id, tenant_id=None):
        return self._documents.get(doc_id)

    async def search(self, query, top_k=10, tenant_id=None):
        if query in self._documents:
            return [self._documents[query]]
        return []

    async def batch_search(self, queries, top_k=10, tenant_id=None):
        results = []
        for query in queries:
            if query in self._documents:
                results.append([self._documents[query]])
            else:
                results.append([])
        return results

    async def delete_document(self, doc_id, tenant_id=None):
        if doc_id in self._documents:
            del self._documents[doc_id]
            return True
        return False

    async def count_documents(self, tenant_id=None):
        return len(self._documents)

    async def list_documents(self, page=1, page_size=10, tenant_id=None):
        all_docs = list(self._documents.values())
        start = (page - 1) * page_size
        end = start + page_size
        return all_docs[start:end]

    async def update_document(self, doc_id, tenant_id=None, title=None, content=None, tags=None, source=None):
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
        pass


class MockTenantService:
    """Mock TenantService，基于内存字典。

    为测试提供 Tenant 鉴权：用 X-API-Key 查找 tenant_id。
    """

    def __init__(self, storage_path=None):
        self._tenants = {TEST_API_KEY: {"tenant_id": TEST_TENANT_ID, "api_key": TEST_API_KEY, "name": "Test", "status": "active"}}

    def get_by_api_key(self, api_key):
        if api_key not in self._tenants:
            return None
        t = self._tenants[api_key]
        if t["status"] != "active":
            return None
        return _tenant_from_dict(t)

    def get_by_id(self, tenant_id):
        for t in self._tenants.values():
            if t["tenant_id"] == tenant_id:
                return _tenant_from_dict(t)
        return None

    def ensure_default_tenant(self):
        return self.get_by_id("default") or self.get_by_id(TEST_TENANT_ID)

    def add_tenant(self, tenant_id, api_key, name="Test", status="active"):
        self._tenants[api_key] = {
            "tenant_id": tenant_id, "api_key": api_key,
            "name": name, "status": status,
        }

    def set_status(self, api_key, status):
        if api_key in self._tenants:
            self._tenants[api_key]["status"] = status


def _tenant_from_dict(d):
    """从字典构建 Tenant（只读 mock）。"""
    from src.domain.models import Tenant
    return Tenant(
        tenant_id=d["tenant_id"], api_key=d["api_key"],
        name=d["name"], status=d["status"],
    )


class MockVectorStore:
    def count(self, tenant_id=None):
        return 0
    async def acount(self, tenant_id=None):
        return 0


class MockEmbeddingService:
    def dimension(self):
        return 768


class MockLLMClient:
    @property
    def is_configured(self):
        return True
    async def generate_answer(self, query, context, **kwargs):
        return "Mock LLM 回答"
    async def stream_answer(self, query, context, **kwargs):
        for ch in "Mock LLM 回答":
            yield ch
    async def close(self):
        pass


# ================================================================
# TestClient Fixture（多租户适配）
# ================================================================

@pytest.fixture
def client():
    """创建测试客户端，使用 Mock 组件。

    多租户适配要点：
      - 使用 app.state.retriever_factory 按租户获取 Retriever
      - 注入 MockTenantService 供 resolve_tenant() 使用
      - app.state.api_key 用于 _verify_admin_key()
      - 所有业务接口请求需携带 X-API-Key: {TEST_API_KEY}
    """
    from src.utils.limiter import limiter
    from src.api.routes import _get_tenant_service

    # 创建测试用的租户服务
    test_tenant_svc = MockTenantService()

    # 覆盖 routes 中的 _get_tenant_service()，返回 mock 实例
    import src.api.routes as routes_module
    _original_get_tenant = routes_module._get_tenant_service
    routes_module._get_tenant_service = lambda: test_tenant_svc

    # 创建 MockRetriever 和 Factory
    mock_retriever = MockRetriever()
    mock_factory = MockFactory(mock_retriever)

    test_app = FastAPI(title="OpenAsk Test", version="1.0.0")

    # 注入 Mock 组件到 app.state
    test_app.state.retriever_factory = mock_factory
    test_app.state.knowledge_service = MockKnowledgeService()
    test_app.state.vector_store = MockVectorStore()
    test_app.state.embedding_service = MockEmbeddingService()
    test_app.state.llm_client = MockLLMClient()
    test_app.state.limiter = limiter
    test_app.state.api_key = TEST_API_KEY  # admin key

    test_app.include_router(router)

    with TestClient(test_app) as client:
        yield client

    # 恢复原始函数
    routes_module._get_tenant_service = _original_get_tenant


# ================================================================
# TestHealthEndpoint
# ================================================================

class TestHealthEndpoint:
    """健康检查端点测试 — 免鉴权。"""

    def test_health_check(self, client):
        """测试健康检查返回正常（无需 API Key）。"""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("healthy", "degraded")
        assert data["version"] == "1.0.0"
        assert "timestamp" in data
        assert "zvec_status" in data
        assert "embedding_status" in data
        assert "llm_status" in data
        assert "cache_status" in data


# ================================================================
# TestChatEndpoint
# ================================================================

class TestChatEndpoint:
    """聊天端点测试 — 需 X-API-Key。"""

    def test_chat_success(self, client):
        response = client.post(
            "/api/chat",
            json={"query": "测试问题", "top_k": 2},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["answer"] == "基于文档生成的回答"
        assert len(data["sources"]) == 2
        assert not data["cache_hit"]
        assert data["llm_used"]

    def test_chat_cache_hit(self, client):
        response = client.post(
            "/api/chat",
            json={"query": "cache_hit_query", "top_k": 2},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["answer"] == "缓存的回答"
        assert data["cache_hit"]
        assert not data["llm_used"]

    def test_chat_empty_query(self, client):
        """空查询 → 422。"""
        response = client.post(
            "/api/chat",
            json={"query": "", "top_k": 2},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 422

    def test_chat_missing_query(self, client):
        """缺少 query 参数 → 422。"""
        response = client.post(
            "/api/chat",
            json={"top_k": 2},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 422

    def test_chat_no_api_key(self, client):
        """未携带 API Key → 401。"""
        response = client.post(
            "/api/chat",
            json={"query": "测试问题", "top_k": 2},
        )
        assert response.status_code == 401

    def test_chat_invalid_api_key(self, client):
        """错误 API Key → 401。"""
        response = client.post(
            "/api/chat",
            json={"query": "测试问题", "top_k": 2},
            headers={"X-API-Key": "sk_invalid"},
        )
        assert response.status_code == 401


# ================================================================
# TestKnowledgeEndpoints
# ================================================================

class TestKnowledgeEndpoints:
    """知识库端点测试 — 需 X-API-Key，tenant_id 透传。"""

    def test_create_document(self, client):
        response = client.post(
            "/api/knowledge",
            json={
                "title": "测试标题",
                "content": "测试内容",
                "tags": ["tag1", "tag2"],
                "source": "manual",
            },
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "测试标题"
        assert data["content"] == "测试内容"
        assert data["tags"] == ["tag1", "tag2"]

    def test_create_document_missing_fields(self, client):
        response = client.post(
            "/api/knowledge",
            json={"title": "测试标题"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 422

    def test_get_document(self, client):
        create_response = client.post(
            "/api/knowledge",
            json={"title": "测试标题", "content": "测试内容"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        doc_id = create_response.json()["doc_id"]

        get_response = client.get(
            f"/api/knowledge/{doc_id}",
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert get_response.status_code == 200
        data = get_response.json()
        assert data["doc_id"] == doc_id
        assert data["title"] == "测试标题"

    def test_get_document_not_found(self, client):
        response = client.get(
            "/api/knowledge/nonexistent",
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 404

    def test_delete_document(self, client):
        create_response = client.post(
            "/api/knowledge",
            json={"title": "测试标题", "content": "测试内容"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        doc_id = create_response.json()["doc_id"]

        delete_response = client.delete(
            f"/api/knowledge/{doc_id}",
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert delete_response.status_code == 200
        data = delete_response.json()
        assert data["success"] is True

    def test_delete_document_not_found(self, client):
        """删除不存在的文档返回 200 + success=False。"""
        response = client.delete(
            "/api/knowledge/nonexistent",
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_search(self, client):
        response = client.post(
            "/api/search",
            json={"query": "测试", "top_k": 5},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_documents(self, client):
        for i in range(3):
            client.post(
                "/api/knowledge",
                json={"title": f"文档{i}", "content": f"内容{i}"},
                headers={"X-API-Key": TEST_API_KEY},
            )

        response = client.get(
            "/api/knowledge?page=1&page_size=10",
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) == 3
        assert data["total"] == 3

    def test_update_document(self, client):
        create_response = client.post(
            "/api/knowledge",
            json={"title": "原始标题", "content": "原始内容"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        doc_id = create_response.json()["doc_id"]

        update_response = client.put(
            f"/api/knowledge/{doc_id}",
            json={"title": "更新后的标题", "tags": ["updated"]},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert update_response.status_code == 200
        data = update_response.json()
        assert data["doc_id"] == doc_id
        assert data["title"] == "更新后的标题"
        assert data["content"] == "原始内容"
        assert data["tags"] == ["updated"]

    def test_update_document_not_found(self, client):
        response = client.put(
            "/api/knowledge/nonexistent",
            json={"title": "新标题"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert response.status_code == 404


# ================================================================
# TestKnowledgeAuth
# ================================================================

class TestKnowledgeAuth:
    """知识库鉴权测试 — 验证 X-API-Key 要求。"""

    def test_create_no_api_key(self, client):
        """无 API Key 创建文档 → 401。"""
        response = client.post(
            "/api/knowledge",
            json={"title": "测试", "content": "内容"},
        )
        assert response.status_code == 401

    def test_get_no_api_key(self, client):
        response = client.get("/api/knowledge/doc1")
        assert response.status_code == 401

    def test_search_no_api_key(self, client):
        response = client.post(
            "/api/search",
            json={"query": "测试", "top_k": 5},
        )
        assert response.status_code == 401

    def test_list_no_api_key(self, client):
        response = client.get("/api/knowledge")
        assert response.status_code == 401

    def test_delete_no_api_key(self, client):
        response = client.delete("/api/knowledge/doc1")
        assert response.status_code == 401


# ================================================================
# TestHealthAuth
# ================================================================

class TestHealthAuth:
    """健康检查鉴权测试 — 验证免鉴权。"""

    def test_health_without_key(self, client):
        """健康检查无 key 也应成功。"""
        response = client.get("/api/health")
        assert response.status_code == 200
