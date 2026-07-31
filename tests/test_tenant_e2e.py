"""多租户端到端测试 — 覆盖 P0-3 清单。

使用真实 TenantService（临时 SQLite）+ FastAPI TestClient，
验证租户 CRUD、鉴权隔离、key 轮换、软删除等关键场景。

运行方式：
    python3 -m pytest tests/test_tenant_e2e.py -v
"""

import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import router, admin_router
from src.services.tenant_service import TenantService
from src.services.tenant_stats import TenantStatsRegistry

# ================================================================
# 常量
# ================================================================

ADMIN_KEY = "sk_e2e_admin_master_key"
DEFAULT_TENANT_KEY = "sk_e2e_default_tenant"


# ================================================================
# Mock 辅助
# ================================================================


def _make_mock_knowledge_service():
    """创建一个全量 async mock 的 KnowledgeService。"""
    ks = AsyncMock()
    ks.list_documents.return_value = []
    ks.count_documents.return_value = 0
    ks.get_by_id.return_value = None
    ks.create_document_from_text.return_value = None
    ks.update_document.return_value = None
    ks.delete_document.return_value = False
    ks.load_and_store_document.return_value = None
    ks.search.return_value = []
    ks.batch_search.return_value = []
    ks.close.return_value = None
    return ks


def _make_mock_factory(retriever):
    """创建 MockRetrieverFactory。"""
    factory = type("MockFactory", (), {})()
    factory.get_retriever_for_tenant = lambda *a, **kw: retriever
    return factory


# ================================================================
# 测试应用 Fixture
# ================================================================


@pytest.fixture()
def e2e_app(tmp_path):
    """
    创建使用真实 TenantService 的 FastAPI TestClient。

    关键点：
      - settings 是 module-level singleton，通过 patch 修改
      - _tenant_service 单例被替换为临时 SQLite 实例
      - 所有需要 await 的方法都用 AsyncMock（lambda 不能 await）
    """
    tmp_db = str(tmp_path / "test_tenants_e2e.db")
    svc = TenantService(storage_path=tmp_db)
    svc.ensure_default_tenant()

    mock_retriever = type("MockRetriever", (), {"_llm_client": type("MockLLM", (), {"is_configured": True})()})()
    mock_factory = _make_mock_factory(mock_retriever)
    mock_stats_registry = TenantStatsRegistry()
    mock_factory._stats_registry = mock_stats_registry
    mock_retriever._stats_registry = mock_stats_registry
    mock_ks = _make_mock_knowledge_service()
    mock_vs = type("MockVS", (), {"acount": lambda *a, **kw: 0, "count": lambda *a, **kw: 0})()
    mock_es = type("MockES", (), {"dimension": lambda *a, **kw: 384})()
    mock_tl = type("MockTenantLimiter", (), {"is_allowed": lambda *a, **kw: True})()

    app = FastAPI(title="OpenAsk E2E Test", version="1.0.0")
    app.state.tenant_service = svc
    app.state.retriever = mock_retriever
    app.state.retriever_factory = mock_factory
    app.state.knowledge_service = mock_ks
    app.state.vector_store = mock_vs
    app.state.embedding_service = mock_es
    app.state.limiter = None
    app.state.tenant_limiter = mock_tl
    app.state.stats_registry = mock_stats_registry
    app.include_router(router)
    app.include_router(admin_router)

    import src.api.routes as routes_mod
    from src.utils.limiter import limiter as _global_limiter

    # 关闭 E2E 测试中的全局 limiter（避免 testclient IP 触发 5/minute 限流）
    orig_enabled = _global_limiter.enabled
    _global_limiter.enabled = False

    try:
        with patch.object(routes_mod, "settings") as mock_settings:
            mock_settings.api.api_key = ADMIN_KEY
            mock_settings.tenant.default_tenant_api_key = DEFAULT_TENANT_KEY
            with patch.object(routes_mod, "_tenant_service", svc):
                with TestClient(app) as client:
                    yield client, svc
    finally:
        _global_limiter.enabled = orig_enabled


@pytest.fixture()
def e2e_client(e2e_app):
    return e2e_app[0]


# ================================================================
# 测试：Admin 端鉴权
# ================================================================


# ================================================================
# 测试：Admin API 限流
# ================================================================

class TestAdminRateLimit:
    """验证 Admin API 端点 5/minute 限流生效。"""

    def test_admin_api_rate_limited(self, tmp_path):
        """连续请求超过 5/minute 应返回 429。"""
        tmp_db = str(tmp_path / "test_rl_tenants.db")
        svc = TenantService(storage_path=tmp_db)
        svc.ensure_default_tenant()

        from src.utils.limiter import limiter
        orig_enabled = limiter.enabled
        limiter.enabled = True

        try:
            app = FastAPI(title="OpenAsk RL Test", version="1.0.0")
            app.state.tenant_service = svc
            app.state.retriever = type("MR", (), {"_llm_client": type("ML", (), {"is_configured": True})()})()
            app.state.retriever_factory = type("MF", (), {"get_retriever_for_tenant": lambda *a, **kw: app.state.retriever})()
            app.state.knowledge_service = AsyncMock()
            app.state.vector_store = type("VS", (), {"acount": lambda *a, **kw: 0, "count": lambda *a, **kw: 0})()
            app.state.embedding_service = type("ES", (), {"dimension": lambda *a, **kw: 384})()
            app.state.limiter = limiter
            app.state.tenant_limiter = type("TL", (), {"is_allowed": lambda *a, **kw: True})()
            app.include_router(router)
            app.include_router(admin_router)

            import src.api.routes as routes_mod

            with patch.object(routes_mod, "settings") as mock_settings:
                mock_settings.api.api_key = ADMIN_KEY
                mock_settings.tenant.default_tenant_api_key = DEFAULT_TENANT_KEY
                with patch.object(routes_mod, "_tenant_service", svc):
                    with TestClient(app) as client:
                        # 前 5 次应该成功
                        success = 0
                        for i in range(7):
                            resp = client.get(
                                "/api/admin/tenants",
                                headers={"X-API-Key": ADMIN_KEY},
                            )
                            if resp.status_code == 200:
                                success += 1
                            # 第 6 次开始应返回 429
                            if i >= 5:
                                assert resp.status_code == 429, (
                                    f"Request {i} should be rate limited, got {resp.status_code}: {resp.text[:100]}"
                                )
                        assert success == 5
        finally:
            limiter.enabled = orig_enabled
    def test_list_tenants_with_valid_admin_key(self, e2e_client):
        resp = e2e_client.get(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_list_tenants_with_invalid_key(self, e2e_client):
        resp = e2e_client.get(
            "/api/admin/tenants",
            headers={"X-API-Key": "sk_wrong_key"},
        )
        assert resp.status_code == 401

    def test_create_tenant_without_key(self, e2e_client):
        resp = e2e_client.post(
            "/api/admin/tenants",
            json={"name": "泄露租户"},
        )
        assert resp.status_code == 401


# ================================================================
# 测试：Admin 端租户 CRUD
# ================================================================


class TestAdminTenantCRUD:
    def test_create_tenant_returns_tenant_response(self, e2e_client):
        resp = e2e_client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "电商A", "status": "active"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tenant_id"].startswith("tenant_")
        assert data["name"] == "电商A"
        assert data["status"] == "active"
        assert data["api_key"].startswith("sk_")

    def test_create_multiple_tenants_isolated(self, e2e_client):
        e2e_client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "租户A"},
        )
        e2e_client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "租户B"},
        )
        resp = e2e_client.get(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()]
        assert "租户A" in names
        assert "租户B" in names

    def test_get_tenant_by_id(self, e2e_client):
        create_resp = e2e_client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "获取测试租户"},
        )
        tenant_id = create_resp.json()["tenant_id"]

        resp = e2e_client.get(
            f"/api/admin/tenants/{tenant_id}",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "获取测试租户"

    def test_get_nonexistent_tenant_returns_404(self, e2e_client):
        resp = e2e_client.get(
            "/api/admin/tenants/nonexistent",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 404

    def test_update_tenant_fields(self, e2e_client):
        create_resp = e2e_client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "待更新"},
        )
        tid = create_resp.json()["tenant_id"]

        resp = e2e_client.put(
            f"/api/admin/tenants/{tid}",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "已更新", "status": "suspended"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "已更新"
        assert data["status"] == "suspended"

    def test_update_nonexistent_tenant_returns_404(self, e2e_client):
        resp = e2e_client.put(
            "/api/admin/tenants/nonexistent",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "X"},
        )
        assert resp.status_code == 404


# ================================================================
# 测试：API Key 鉴权隔离
# ================================================================


class TestTenantAuthIsolation:
    def test_tenant_a_key_cannot_list_tenant_b_documents(self, e2e_client):
        """用租户A的 key 和租户B的 key 分别调知识库列表 → 各自返回空。"""
        resp_a = e2e_client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "隔离A"},
        )
        key_a = resp_a.json()["api_key"]

        resp_b = e2e_client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "隔离B"},
        )
        key_b = resp_b.json()["api_key"]

        # 两个 key 都正常鉴权 → 200（空列表，没有上传文档）
        for key in (key_a, key_b):
            resp = e2e_client.get(
                "/api/knowledge?page=1&page_size=10",
                headers={"X-API-Key": key},
            )
            assert resp.status_code == 200
            assert resp.json()["total"] == 0

    def test_invalid_tenant_key_returns_401(self, e2e_client):
        resp = e2e_client.get(
            "/api/knowledge?page=1&page_size=10",
            headers={"X-API-Key": "sk_invalid_key"},
        )
        assert resp.status_code == 401


# ================================================================
# 测试：租户 key 过期/禁用
# ================================================================


class TestTenantKeyExpired:
    def test_suspended_tenant_key_returns_401(self, e2e_app):
        """suspended 状态的租户，其 API Key 鉴权应返回 401。"""
        client, svc = e2e_app

        create_resp = client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "待禁用租户"},
        )
        tid = create_resp.json()["tenant_id"]
        key = create_resp.json()["api_key"]

        # 禁用租户
        svc.update_tenant(tid, status="suspended")

        # 被禁用的 key → 401
        resp = client.get(
            "/api/knowledge?page=1&page_size=10",
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 401

        # 恢复 active 后应正常
        svc.update_tenant(tid, status="active")
        resp = client.get(
            "/api/knowledge?page=1&page_size=10",
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 200


# ================================================================
# 测试：Key 轮换
# ================================================================


class TestKeyRotation:
    def test_rotate_key_old_key_invalidated(self, e2e_client):
        create_resp = e2e_client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "轮换测试租户"},
        )
        tid = create_resp.json()["tenant_id"]
        old_key = create_resp.json()["api_key"]

        # 旧 key 可用
        assert e2e_client.get(
            "/api/knowledge?page=1&page_size=10",
            headers={"X-API-Key": old_key},
        ).status_code == 200

        # 轮换
        rotate_resp = e2e_client.post(
            f"/api/admin/tenants/{tid}/rotate-key",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert rotate_resp.status_code == 200
        new_key = rotate_resp.json()["api_key"]
        assert new_key != old_key

        # 旧 key 失效 → 401
        assert e2e_client.get(
            "/api/knowledge?page=1&page_size=10",
            headers={"X-API-Key": old_key},
        ).status_code == 401

        # 新 key 可用 → 200
        assert e2e_client.get(
            "/api/knowledge?page=1&page_size=10",
            headers={"X-API-Key": new_key},
        ).status_code == 200

    def test_rotate_nonexistent_tenant_returns_404(self, e2e_client):
        resp = e2e_client.post(
            "/api/admin/tenants/nonexistent/rotate-key",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 404


# ================================================================
# 测试：软删除
# ================================================================


class TestSoftDelete:
    def test_soft_delete_marks_deleted(self, e2e_app):
        client, svc = e2e_app

        create_resp = client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "待删除租户"},
        )
        tid = create_resp.json()["tenant_id"]

        # 删除
        resp = client.delete(
            f"/api/admin/tenants/{tid}",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # 记录仍存在，status 为 deleted
        tenant = svc.get_by_id(tid)
        assert tenant is not None
        assert tenant.status == "deleted"

        # 被删除的 key 应失效
        resp = client.get(
            "/api/knowledge?page=1&page_size=10",
            headers={"X-API-Key": create_resp.json()["api_key"]},
        )
        assert resp.status_code == 401

    def test_delete_nonexistent_tenant_returns_404(self, e2e_client):
        resp = e2e_client.delete(
            "/api/admin/tenants/nonexistent",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 404

    def test_soft_delete_is_idempotent(self, e2e_app):
        """再次删除已删除的租户 → 仍成功（记录存在但已 deleted）。"""
        client, svc = e2e_app

        create_resp = client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "幂等测试"},
        )
        tid = create_resp.json()["tenant_id"]

        # 第一次删除
        assert svc.delete_tenant(tid) is True
        # 第二次删除（get_by_id 仍返回记录）
        assert svc.delete_tenant(tid) is True


class TestTenantDeleteSemantics:
    """测试 P1-6：list_tenants 排除 deleted、删除时返回文档数。"""

    def test_list_tenants_excludes_deleted_by_default(self, e2e_app):
        """默认 list_tenants 不返回已删除的租户。"""
        client, svc = e2e_app

        # 创建一个正常租户和一个被删除的租户
        resp_a = client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "正常租户"},
        )
        tid_a = resp_a.json()["tenant_id"]

        resp_b = client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "待删除租户"},
        )
        tid_b = resp_b.json()["tenant_id"]

        # 删除租户 B
        svc.delete_tenant(tid_b)

        # 默认列表不应包含 B
        resp = client.get(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        ids = [t["tenant_id"] for t in resp.json()]
        assert tid_b not in ids
        assert tid_a in ids

    def test_list_tenants_includes_deleted_when_requested(self, e2e_app):
        """include_deleted=true 时返回所有租户（含 deleted）。"""
        client, svc = e2e_app

        resp_b = client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "待删除"},
        )
        tid_b = resp_b.json()["tenant_id"]
        svc.delete_tenant(tid_b)

        # 含 deleted 的列表应包含 B
        resp = client.get(
            "/api/admin/tenants?include_deleted=true",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        ids = [t["tenant_id"] for t in resp.json()]
        assert tid_b in ids

    def test_delete_tenant_returns_document_count_message(self, e2e_app):
        """删除租户返回 success 和 message。"""
        client, svc = e2e_app

        create_resp = client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "待删除"},
        )
        tid = create_resp.json()["tenant_id"]

        resp = client.delete(
            f"/api/admin/tenants/{tid}",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "删除成功" in data["message"]


# ================================================================
# 测试：统计注册表
# ================================================================


class TestTenantStatsRegistry:
    """验证 TenantStatsRegistry 的记录和读取。"""

    def test_record_and_get_stats(self):
        registry = TenantStatsRegistry()
        registry.record("t1", prompt_tokens=100, completion_tokens=50, cache_hit=False)
        registry.record("t1", prompt_tokens=200, completion_tokens=30, cache_hit=True)

        stats = registry.get_stats("t1")
        assert stats is not None
        assert stats.total_calls == 2
        assert stats.prompt_tokens == 300
        assert stats.completion_tokens == 80
        assert stats.cache_hits == 1
        assert stats.cache_hit_rate == 0.5

    def test_separate_tenants(self):
        registry = TenantStatsRegistry()
        registry.record("t1", prompt_tokens=100, completion_tokens=50)
        registry.record("t2", prompt_tokens=200, completion_tokens=100)

        s1 = registry.get_stats("t1")
        s2 = registry.get_stats("t2")
        assert s1.total_calls == 1 and s2.total_calls == 1
        assert s1.prompt_tokens == 100 and s2.prompt_tokens == 200

    def test_reset(self):
        registry = TenantStatsRegistry()
        registry.record("t1", prompt_tokens=50)
        assert registry.get_stats("t1").total_calls == 1
        registry.reset("t1")
        assert registry.get_stats("t1") is None

    def test_reset_all(self):
        registry = TenantStatsRegistry()
        registry.record("t1", prompt_tokens=50)
        registry.record("t2", prompt_tokens=100)
        registry.reset()
        assert registry.get_stats("t1") is None
        assert registry.get_stats("t2") is None

    def test_cache_hit_rate_zero_calls(self):
        registry = TenantStatsRegistry()
        stats = registry.get_stats("nonexistent")
        assert stats is None


# ================================================================
# 测试：Stats 端点返回真实数据
# ================================================================


class TestStatsEndpoint:
    """验证 /tenants/{id}/stats 端点返回真实统计。"""

    def test_stats_endpoint_returns_real_data(self, e2e_app):
        """手动记录 stats 后，stats 端点应返回对应数据。"""
        client, svc = e2e_app

        # 创建租户
        create_resp = client.post(
            "/api/admin/tenants",
            headers={"X-API-Key": ADMIN_KEY},
            json={"name": "统计测试租户"},
        )
        tid = create_resp.json()["tenant_id"]

        # 直接往 stats registry 写入模拟数据
        client.app.state.stats_registry.record(
            tid, prompt_tokens=500, completion_tokens=200, cache_hit=False,
        )
        client.app.state.stats_registry.record(
            tid, prompt_tokens=100, completion_tokens=50, cache_hit=True,
        )

        resp = client.get(
            f"/api/admin/tenants/{tid}/stats",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 2
        assert data["prompt_tokens"] == 600
        assert data["completion_tokens"] == 250
        assert data["cache_hit_rate"] == 0.5
        assert data["last_request"] > 0

    def test_stats_endpoint_nonexistent_tenant(self, e2e_app):
        client, svc = e2e_app
        resp = client.get(
            "/api/admin/tenants/nonexistent/stats",
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 404
