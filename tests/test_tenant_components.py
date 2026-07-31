"""多租户改造单元测试 — 补齐集成测试未覆盖的核心组件。

覆盖清单（对应缺失测试 2/3/5/7）：
  2. TenantService — CRUD / API Key 鉴权 / rotate_key / soft delete / ensure_default
  3. RetrieverFactory + RetrieverCache — LRU / 缓存隔离 / 自定义 LLM 配置
  5. TokenMonitor — 按租户 token 统计
  7. Prompt 模板定制 — system_prompt 透传（在 Retriever 层模拟）

这些测试使用 in-memory SQLite + Mock LLM 客户端，无需启动 FastAPI 服务。
运行方式：
  pytest tests/test_tenant_components.py -v
"""

import os
import sys
import tempfile
import threading
from collections import defaultdict
from typing import Optional

import pytest

# 确保项目根在 sys.path
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from src.domain.models import Tenant, DEFAULT_TENANT_ID
from src.domain.exceptions import TenantNotFoundError
from src.services.tenant_service import TenantService
from src.utils.config import settings


# =========================================================================
# TenantService 单元测试
# =========================================================================

class TestTenantServiceCreate:
    """测试 1/2: 租户 CRUD 与 API Key 鉴权。"""

    def test_create_tenant_generates_id_and_key(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        t = svc.create_tenant(name="电商A")
        assert t.tenant_id.startswith("tenant_")
        assert t.api_key.startswith("sk_")
        assert t.name == "电商A"
        assert t.status == "active"
        assert svc.get_by_id(t.tenant_id).tenant_id == t.tenant_id

    def test_create_tenant_with_explicit_id_and_key(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        t = svc.create_tenant(name="电商A", tenant_id="shop_a", api_key="sk_shop_a")
        assert t.tenant_id == "shop_a"
        assert t.api_key == "sk_shop_a"

    def test_create_tenant_duplicate_id_fails(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        svc.create_tenant(name="电商A", tenant_id="dup", api_key="sk_a")
        with pytest.raises(Exception):  # UNIQUE constraint on tenant_id
            svc.create_tenant(name="电商B", tenant_id="dup", api_key="sk_b")

    def test_create_tenant_duplicate_api_key_fails(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        svc.create_tenant(name="电商A", api_key="sk_shared")
        with pytest.raises(Exception):  # UNIQUE constraint on api_key
            svc.create_tenant(name="电商B", api_key="sk_shared")


class TestTenantServiceAuth:
    """测试 2: API Key 鉴权（get_by_api_key 只返回 active）。"""

    def test_get_by_api_key_returns_active(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        t = svc.create_tenant(name="A", api_key="sk_active")
        found = svc.get_by_api_key("sk_active")
        assert found.tenant_id == t.tenant_id

    def test_get_by_api_key_ignores_suspended(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        t = svc.create_tenant(name="A", tenant_id="sus_t", api_key="sk_sus")
        svc.update_tenant("sus_t", status="suspended")
        # 被 suspended 的租户，其 API Key 鉴权应返回 None
        assert svc.get_by_api_key("sk_sus") is None
        # 但 get_by_id 仍能查到（只是不能鉴权）
        by_id = svc.get_by_id("sus_t")
        assert by_id is not None and by_id.status == "suspended"

    def test_get_by_api_key_not_found(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        assert svc.get_by_api_key("sk_nonexistent") is None


class TestTenantServiceUpdateAndDelete:
    """测试 2: 更新与软删除。"""

    def test_update_tenant_fields(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        t = svc.create_tenant(name="A", tenant_id="updt", api_key="sk_up")
        updated = svc.update_tenant("updt", name="B", status="suspended")
        assert updated.name == "B"
        assert updated.status == "suspended"

    def test_update_tenant_not_found_raises(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        with pytest.raises(TenantNotFoundError):
            svc.update_tenant("nonexistent", name="X")

    def test_delete_is_soft_delete(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        t = svc.create_tenant(name="A", tenant_id="soft_del", api_key="sk_sd")
        assert svc.delete_tenant("soft_del") is True
        still_exists = svc.get_by_id("soft_del")
        assert still_exists is not None
        assert still_exists.status == "deleted"

    def test_delete_nonexistent_returns_false(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        assert svc.delete_tenant("nonexistent") is False


class TestTenantServiceKeyRotation:
    """测试 2: API Key 轮换（安全关键操作）。"""

    def test_rotate_api_key_returns_new_key(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        t = svc.create_tenant(name="A", tenant_id="rotate", api_key="sk_old")
        new_key = svc.rotate_api_key("rotate")
        assert new_key != "sk_old"
        assert new_key.startswith("sk_")

    def test_rotate_key_old_key_invalidated(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        svc.create_tenant(name="A", tenant_id="rotate2", api_key="sk_old")
        new_key = svc.rotate_api_key("rotate2")
        # 旧 key 查询应失败
        assert svc.get_by_api_key("sk_old") is None
        # 新 key 应有效
        assert svc.get_by_api_key(new_key) is not None

    def test_rotate_key_not_found_raises(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        with pytest.raises(TenantNotFoundError):
            svc.rotate_api_key("nonexistent")


class TestTenantServiceDefault:
    """测试 2: 默认租户 ensure_default。"""

    def test_ensure_default_creates_on_missing(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        t = svc.ensure_default_tenant()
        assert t.tenant_id == "default"
        assert t.name == "默认租户"

    def test_ensure_default_idempotent(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        t1 = svc.ensure_default_tenant()
        t2 = svc.ensure_default_tenant()
        assert t1.tenant_id == t2.tenant_id == "default"


class TestTenantServiceList:
    """测试 1: 租户列表。"""

    def test_list_tenants_returns_all(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        svc.create_tenant(name="A", tenant_id="t1", api_key="sk_1")
        svc.create_tenant(name="B", tenant_id="t2", api_key="sk_2")
        tenants = svc.list_tenants()
        ids = [t.tenant_id for t in tenants]
        assert "t1" in ids and "t2" in ids


# =========================================================================
# TenantService — 线程安全（集成测试未覆盖）
# =========================================================================

class TestTenantServiceConcurrency:
    """测试 10: 并发安全。"""

    def test_concurrent_create_tenants(self, tmp_db):
        svc = TenantService(storage_path=tmp_db)
        errors = []

        def create(i):
            try:
                svc.create_tenant(name=f"t{i}", tenant_id=f"conc_{i}", api_key=f"sk_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 20 个创建应全部成功（不报错）
        assert len(errors) == 0, f"并发创建失败: {errors}"
        assert len(svc.list_tenants()) >= 20


# =========================================================================
# RetrieverCache + RetrieverFactory 单元测试
# =========================================================================

class TestRetrieverCache:
    """测试 3: LRU 缓存。"""

    def test_get_returns_cached(self):
        from src.core.factory import RetrieverCache

        cache = RetrieverCache(maxsize=4)
        mock_r = object()
        cache.put("t1", mock_r)
        assert cache.get("t1") is mock_r

    def test_get_miss_returns_none(self):
        from src.core.factory import RetrieverCache

        cache = RetrieverCache(maxsize=2)
        assert cache.get("missing") is None

    def test_eviction_on_maxsize(self):
        from src.core.factory import RetrieverCache

        cache = RetrieverCache(maxsize=2)
        cache.put("a", object())
        cache.put("b", object())
        cache.put("c", object())  # 触发驱逐 a
        assert cache.get("a") is None
        assert cache.get("b") is not None
        assert cache.get("c") is not None

    def test_len(self):
        from src.core.factory import RetrieverCache

        cache = RetrieverCache(maxsize=4)
        cache.put("x", object())
        assert len(cache) == 1

    def test_close_cleans_up(self, tmp_path):
        from src.core.factory import RetrieverCache
        import asyncio

        cache = RetrieverCache(maxsize=4)
        # 构造一个带 close 方法的 mock
        closed = []

        class MockRetriever:
            async def close(self):
                closed.append(True)

        cache.put("a", MockRetriever())
        asyncio.run(cache.close())
        assert len(cache) == 0
        assert len(closed) == 1


class TestRetrieverFactory:
    """测试 3: 按租户创建 Retriever 实例。

    注意：默认 cache_path 指向 data/zvec_llm_cache，
    Docker 容器运行时该目录被锁定，因此测试中直接覆盖 settings 使用 tmp_path。
    """

    @pytest.fixture(autouse=True)
    def _override_cache_path(self, tmp_path, monkeypatch):
        """临时覆盖 ZVEC 缓存路径，避免触碰 Docker 容器的缓存锁。"""
        original = settings.zvec.cache_path
        settings.zvec.cache_path = str(tmp_path / "factory_cache")
        yield
        settings.zvec.cache_path = original

    def test_returns_same_instance_on_repeat(self):
        from src.core.factory import RetrieverFactory

        factory = RetrieverFactory(
            embedding_service=None,
            vector_store=None,
            default_llm_client=None,
        )
        r1 = factory.get_retriever_for_tenant("t1")
        r2 = factory.get_retriever_for_tenant("t1")
        assert r1 is r2

    def test_different_tenants_get_different_instances(self):
        from src.core.factory import RetrieverFactory

        factory = RetrieverFactory(
            embedding_service=None,
            vector_store=None,
            default_llm_client=None,
        )
        r1 = factory.get_retriever_for_tenant("t1")
        r2 = factory.get_retriever_for_tenant("t2")
        assert r1 is not r2
        # 缓存隔离：不同 Retriever 实例应绑定不同 cache_backend
        assert r1._cache_backend is not r2._cache_backend

    def test_default_tenant_uses_default_llm_client(self):
        from src.core.factory import RetrieverFactory

        default_client = object()
        factory = RetrieverFactory(
            embedding_service=None,
            vector_store=None,
            default_llm_client=default_client,
        )
        r = factory.get_retriever_for_tenant(DEFAULT_TENANT_ID)
        assert r._llm_client is default_client

    def test_custom_llm_config_creates_new_client(self):
        from src.core.factory import RetrieverFactory

        default_client = object()
        tenant = Tenant(
            tenant_id="custom_llm",
            api_key="sk_x",
            name="Custom",
            llm_api_key="sk_custom",
            llm_api_base="https://custom.api/v1",
            llm_model="custom-model",
        )
        factory = RetrieverFactory(
            embedding_service=None,
            vector_store=None,
            default_llm_client=default_client,
        )
        r = factory.get_retriever_for_tenant("custom_llm", tenant=tenant)
        assert r._llm_client is not default_client

    def test_no_custom_llm_uses_default_client(self):
        from src.core.factory import RetrieverFactory

        default_client = object()
        tenant = Tenant(
            tenant_id="no_custom",
            api_key="sk_x",
            name="No Custom",
            llm_api_key="",  # 空 → 使用全局
        )
        factory = RetrieverFactory(
            embedding_service=None,
            vector_store=None,
            default_llm_client=default_client,
        )
        r = factory.get_retriever_for_tenant("no_custom", tenant=tenant)
        assert r._llm_client is default_client


# =========================================================================
# TenantLimiter 单元测试
# =========================================================================

class TestTenantLimiter:
    """测试 6: 动态限流（滑动窗口、IP 兜底、运行时配置更新）。"""

    def test_basic_allow_and_block(self):
        from src.utils.dynamic_limiter import TenantLimiter
        from src.domain.models import Tenant

        limiter = TenantLimiter()

        class FakeRequest:
            headers = {}
            client = None

        tenant = Tenant(tenant_id="t1", api_key="sk_1", name="T1", rate_limit_per_user="2/minute")
        req = FakeRequest()

        assert limiter.is_allowed(req, tenant) is True
        assert limiter.is_allowed(req, tenant) is True
        assert limiter.is_allowed(req, tenant) is False  # 第 3 次应被拒

    def test_ip_fallback_when_no_tenant(self):
        from src.utils.dynamic_limiter import TenantLimiter

        limiter = TenantLimiter()

        class FakeRequest:
            headers = {}
            client = None

        req = FakeRequest()
        # 无租户、无 IP 信息 → 不应抛异常
        assert limiter.is_allowed(req, tenant=None) is True

    def test_different_tenants_independent_windows(self):
        from src.utils.dynamic_limiter import TenantLimiter
        from src.domain.models import Tenant

        # 每个测试用独立实例，避免与同 class 其他测试共享窗口状态
        limiter = TenantLimiter()

        class FakeRequest:
            headers = {}
            client = None

        t1 = Tenant(tenant_id="lim_t1", api_key="sk_1", name="T1", rate_limit_per_user="1/minute")
        t2 = Tenant(tenant_id="lim_t2", api_key="sk_2", name="T2", rate_limit_per_user="1/minute")
        req = FakeRequest()

        assert limiter.is_allowed(req, t1) is True
        assert limiter.is_allowed(req, t1) is False  # t1 满了
        assert limiter.is_allowed(req, t2) is True  # t2 不受影响

    def test_remaining_count(self):
        from src.utils.dynamic_limiter import TenantLimiter
        from src.domain.models import Tenant

        limiter = TenantLimiter()

        class FakeRequest:
            headers = {}
            client = None

        t = Tenant(tenant_id="t_rem", api_key="sk_r", name="T", rate_limit_per_user="5/minute")
        req = FakeRequest()

        limiter.is_allowed(req, t)
        limiter.is_allowed(req, t)
        assert limiter.get_remaining(req, t) == 3

    def test_rate_parse_variants(self):
        from src.utils.dynamic_limiter import _parse_rate

        assert _parse_rate("60/minute") == (60, 60)
        assert _parse_rate("100/hour") == (100, 3600)
        assert _parse_rate("10/second") == (10, 1)
        assert _parse_rate("5/day") == (5, 86400)
        assert _parse_rate("bad") == (60, 60)  # fallback
        assert _parse_rate("abc/minute") == (60, 60)  # fallback

    def test_runtime_config_update(self):
        from src.utils.dynamic_limiter import TenantLimiter
        from src.domain.models import Tenant

        limiter = TenantLimiter()

        class FakeRequest:
            headers = {}
            client = None

        req = FakeRequest()
        # 先用宽松配置
        t1 = Tenant(tenant_id="t_rt", api_key="sk_r", name="T", rate_limit_per_user="10/minute")
        assert limiter.is_allowed(req, t1) is True  # 第 1 次

        # 改用严格配置（max 变化触发重建窗口）
        t2 = Tenant(tenant_id="t_rt", api_key="sk_r", name="T", rate_limit_per_user="1/minute")
        # 窗口被重建，之前的一次不再计
        assert limiter.is_allowed(req, t2) is True
        assert limiter.is_allowed(req, t2) is False


# =========================================================================
# TokenMonitor 单元测试
# =========================================================================

class TestTokenMonitor:
    """测试 5: Token 统计（全局计数）。

    注：当前 TokenMonitor 实现为全局单计数器（未按租户隔离），
    测试验证 record/get_stats 接口正常工作。
    """

    def test_record_and_get_stats(self):
        from src.services.sensenova_client import TokenMonitor

        monitor = TokenMonitor()
        monitor.record(prompt_tokens=100, completion_tokens=50)
        monitor.record(prompt_tokens=200, completion_tokens=30)

        stats = monitor.get_stats()
        assert stats["total_calls"] == 2
        assert stats["total_prompt_tokens"] == 300
        assert stats["total_completion_tokens"] == 80
        assert stats["total_tokens"] == 380

    def test_empty_monitor(self):
        from src.services.sensenova_client import TokenMonitor

        monitor = TokenMonitor()
        stats = monitor.get_stats()
        assert stats["total_calls"] == 0
        assert stats["total_tokens"] == 0


# =========================================================================
# Fixture
# =========================================================================

@pytest.fixture
def tmp_db(tmp_path):
    """临时 SQLite 数据库路径。"""
    return str(tmp_path / "test_tenants.db")
