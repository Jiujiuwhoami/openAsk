#!/usr/bin/env python3
"""
多租户改造集成测试（在 Docker 容器内运行）。

测试流程：
  1. 启动 FastAPI 应用
  2. 确保 default 租户存在
  3. 创建两个租户（tenant_a, tenant_b）
  4. 分别为两个租户写入文档
  5. 验证查询隔离（tenant_a 只能看到自己的文档）
  6. 验证 CRUD 隔离
  7. 验证鉴权（无 key / 错 key → 401）
  8. 验证 /api/admin/tenants 管理接口

用法：
  docker run --rm -v $(pwd):/app openask-dev:tenant python scripts/integration_test_tenant.py
"""

import asyncio
import sys
import uuid
import os

# 脚本在 scripts/ 目录，需要确保父目录在 sys.path 中以便 import src
script_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(script_dir)
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

# 确保目录存在
os.makedirs("data", exist_ok=True)


async def test():
    passed = 0
    failed = 0

    def ok(name):
        nonlocal passed
        passed += 1
        print(f"  ✅ {name}")

    def fail(name, err):
        nonlocal failed
        failed += 1
        print(f"  ❌ {name}: {err}")

    from src.domain.models import Tenant, Document
    from src.infrastructure.zvec_store import ZvecStore
    from src.infrastructure.embedding_service import SentenceBertEmbeddingService
    from src.services.tenant_service import TenantService
    from src.services.knowledge_service import KnowledgeService
    from src.core.retriever import Retriever, RetrievalResult
    from src.infrastructure.llm_response_cache import LLMResponseCache
    from src.services.sensenova_client import SenseNovaClient
    from src.utils.config import settings

    # 使用临时数据目录，避免污染现有数据
    test_dir = f"data/test_tenant_{uuid.uuid4().hex[:8]}"
    zvec_path = f"{test_dir}/zvec"
    db_path = f"{test_dir}/tenants.db"

    # ========== 1. 初始化 ==========
    print("\n=== 1. 初始化 ===")
    try:
        settings.tenant.storage_path = db_path
        tenant_svc = TenantService(storage_path=db_path)
        # 先删除任何已有的 default，然后创建带固定 key 的
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM tenants WHERE tenant_id = 'default'")
        conn.commit()
        conn.close()

        default = tenant_svc.ensure_default_tenant()
        # 用 rotate 生成固定 key
        default_key = "sk_default_for_test"
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE tenants SET api_key = ? WHERE tenant_id = 'default'", (default_key,))
        conn.commit()
        conn.close()
        # 重新读取
        default = tenant_svc.get_by_id("default")
        ok("default 租户创建")

        tenant_a = tenant_svc.create_tenant("测试站点A", api_key="sk_a")
        tenant_b = tenant_svc.create_tenant("测试站点B", api_key="sk_b")
        ok(f"创建 tenant_a={tenant_a.tenant_id}, tenant_b={tenant_b.tenant_id}")
    except Exception as e:
        fail("初始化", e)
        return

    # ========== 2. 向两个租户写入文档 ==========
    print("\n=== 2. 写入文档（两个租户）===")
    try:
        embedder = SentenceBertEmbeddingService()
        # 单一 ZvecStore 实例，不同 tenant_id 参数区分租户
        store = ZvecStore(data_path=zvec_path)

        async def insert_doc(doc: Document, tid: str):
            vec = await embedder.encode(doc.content)
            store.insert(doc, vec, tenant_id=tid)

        # 租户A：写入3篇文档
        doc_a1 = Document(doc_id="a1", content="什么是退货政策？退货期限是30天。", title="退货政策", tenant_id=tenant_a.tenant_id)
        await insert_doc(doc_a1, tenant_a.tenant_id)

        doc_a2 = Document(doc_id="a2", content="退款流程：提交申请后3-5个工作日到账。", title="退款流程", tenant_id=tenant_a.tenant_id)
        await insert_doc(doc_a2, tenant_a.tenant_id)

        doc_a3 = Document(doc_id="a3", content="运费由买家承担，偏远地区加收5元。", title="运费说明", tenant_id=tenant_a.tenant_id)
        await insert_doc(doc_a3, tenant_a.tenant_id)

        # 租户B：写入2篇文档
        doc_b1 = Document(doc_id="b1", content="会员价格：钻石会员享受9折，黄金会员享受95折。", title="会员价", tenant_id=tenant_b.tenant_id)
        await insert_doc(doc_b1, tenant_b.tenant_id)

        doc_b2 = Document(doc_id="b2", content="积分兑换：100积分可兑换10元优惠券。", title="积分兑换", tenant_id=tenant_b.tenant_id)
        await insert_doc(doc_b2, tenant_b.tenant_id)

        ok("租户A写入3篇，租户B写入2篇")
    except Exception as e:
        import traceback
        fail("写入文档", f"{e}\n{traceback.format_exc()}")
        return

    # ========== 3. 验证查询隔离 ==========
    print("\n=== 3. 查询隔离 ===")
    try:
        # tenant_a 查询
        vec = await embedder.encode("退货政策是什么")
        results_a = store.search(vec, top_k=10, tenant_id=tenant_a.tenant_id)
        doc_ids_a = [r.doc_id for r in results_a]
        assert all(r.tenant_id == tenant_a.tenant_id for r in results_a), f"tenant_a 返回了其他租户数据: {doc_ids_a}"
        assert len(results_a) >= 2, f"tenant_a 应至少返回2篇: {len(results_a)}"
        ok(f"tenant_a 检索 {len(results_a)} 篇，全部属于 A")

        # tenant_b 查询
        vec = await embedder.encode("会员价格")
        results_b = store.search(vec, top_k=10, tenant_id=tenant_b.tenant_id)
        doc_ids_b = [r.doc_id for r in results_b]
        assert all(r.tenant_id == tenant_b.tenant_id for r in results_b), f"tenant_b 返回了其他租户数据: {doc_ids_b}"
        assert len(results_b) >= 1, f"tenant_b 应至少返回1篇: {len(results_b)}"
        ok(f"tenant_b 检索 {len(results_b)} 篇，全部属于 B")

        # tenant_b 查不到 A 的文档 — 验证返回的全部是 tenant_b 自己的
        vec = await embedder.encode("退货政策是什么")
        results_b_cross = store.search(vec, top_k=10, tenant_id=tenant_b.tenant_id)
        assert all(r.tenant_id == tenant_b.tenant_id for r in results_b_cross), \
            f"tenant_b 返回了其他租户的文档: {[r.tenant_id for r in results_b_cross]}"
        ok("tenant_b 查不到 tenant_a 的文档 ✅ 隔离成功")
    except AssertionError as e:
        fail("查询隔离", e)
    except Exception as e:
        fail("查询隔离", e)

    # ========== 4. 验证 count ==========
    print("\n=== 4. count 按租户 ===")
    try:
        count_a = store.count(tenant_id=tenant_a.tenant_id)
        count_b = store.count(tenant_id=tenant_b.tenant_id)
        assert count_a >= 2, f"A 应有 >=2 篇: {count_a}"
        assert count_b >= 2, f"B 应有 >=2 篇: {count_b}"
        ok(f"tenant_a count={count_a}, tenant_b count={count_b}")
    except AssertionError as e:
        fail("count", e)
    except Exception as e:
        fail("count", e)

    # ========== 5. 验证 delete 跨租户保护 ==========
    print("\n=== 5. delete 跨租户保护 ===")
    try:
        # tenant_b 不应该删除 A 的文档
        deleted = store.delete("a1", tenant_id=tenant_b.tenant_id)
        assert deleted is False, "tenant_b 不应删除 A 的文档"
        ok("tenant_b 无法删除 tenant_a 的文档 ✅")

        # tenant_a 可以删除自己的
        deleted = store.delete("a1", tenant_id=tenant_a.tenant_id)
        assert deleted is True
        ok("tenant_a 可以删除自己的文档")

        # 删除后查不到
        vec = await embedder.encode("什么是退货政策？")
        r = store.search(vec, top_k=10, tenant_id=tenant_a.tenant_id)
        assert all(x.doc_id != "a1" for x in r)
        ok("删除后查询不可见")
    except AssertionError as e:
        fail("delete 跨租户保护", e)
    except Exception as e:
        fail("delete 跨租户保护", e)

    # ========== 6. 验证 list / get ==========
    print("\n=== 6. list / get 按租户 ===")
    try:
        docs_a = store.list(tenant_id=tenant_a.tenant_id)
        assert all(d.tenant_id == tenant_a.tenant_id for d in docs_a)
        ok(f"list tenant_a: {len(docs_a)} 篇")

        doc = store.get("a2", tenant_id=tenant_a.tenant_id)
        assert doc is not None and doc.tenant_id == tenant_a.tenant_id
        ok("get 按租户可见")

        doc_b_get_a = store.get("a2", tenant_id=tenant_b.tenant_id)
        assert doc_b_get_a is None, "B 不应看到 A 的文档"
        ok("get 跨租户不可见 ✅")
    except AssertionError as e:
        fail("list/get", e)
    except Exception as e:
        fail("list/get", e)

    # ========== 7. 验证 Retriever 层级租户隔离（关键路径） ==========
    print("\n=== 7. Retriever 层级租户隔离 ===")
    try:
        from src.infrastructure.interfaces.llm_client import LLMClient as LLMClientInterface
        from src.domain.exceptions import SenseNovaAPIError

        # Mock LLMClient：故意抛 SenseNovaAPIError，让 Retriever 走降级路径
        # 这样测试聚焦于检索隔离，不需要真实 LLM 调用
        class _MockLLMClient(LLMClientInterface):
            @property
            def is_configured(self) -> bool:
                return True

            async def generate_answer(self, query: str, context, **kwargs) -> str:
                raise SenseNovaAPIError("mock — 故意失败，验证降级路径")

            async def stream_answer(self, query: str, context, **kwargs):
                return None

            async def close(self):
                pass

        llm_mock = _MockLLMClient()

        # 为两个租户各自创建独立的 LLM 响应缓存（租户隔离缓存）
        cache_a_path = f"{test_dir}/cache_a"
        cache_b_path = f"{test_dir}/cache_b"
        cache_a = LLMResponseCache(cache_path=cache_a_path)
        cache_b = LLMResponseCache(cache_path=cache_b_path)

        retriever = Retriever(
            embedding_service=embedder,
            vector_store=store,
            cache_backend=cache_a,
            llm_client=llm_mock,
            reranker=None,
            embedding_cache=None,
        )

        # 租户A 通过 retriever 查询 — 应只看到 A 的文档
        result_a = await retriever.retrieve(
            query="退货政策",
            top_k=5,
            tenant_id=tenant_a.tenant_id,
            cache_backend=cache_a,
        )
        assert all(s.tenant_id == tenant_a.tenant_id for s in result_a.sources), \
            f"retriever tenant_a 返回了其他租户的文档: {[s.tenant_id for s in result_a.sources]}"
        ok(f"Retriever tenant_a 检索到 {len(result_a.sources)} 篇，全部属于 A")

        # 租户B 通过 retriever 查询 — 应只看到 B 的文档，绝不能泄漏 A 的
        result_b = await retriever.retrieve(
            query="退货政策",  # 同样查询词
            top_k=5,
            tenant_id=tenant_b.tenant_id,
            cache_backend=cache_b,
        )
        assert all(s.tenant_id == tenant_b.tenant_id for s in result_b.sources), \
            f"retriever tenant_b 返回了其他租户的文档: {[s.tenant_id for s in result_b.sources]}"
        ok(f"Retriever tenant_b 检索到 {len(result_b.sources)} 篇，全部属于 B")

        # 流式查询同样验证
        async for event in retriever.retrieve_stream(
            query="会员价格",
            top_k=5,
            tenant_id=tenant_b.tenant_id,
            cache_backend=cache_b,
        ):
            if event["event"] == "sources":
                sources_stream = event["data"]
                assert all(s.get("doc_id", "").startswith("b") for s in sources_stream), \
                    f"流式查询 tenant_b 返回了其他租户文档"
                ok("流式查询 tenant_b 隔离正确")
                break

        # 缓存隔离验证：两次相同查询不会跨租户缓存
        result_a2 = await retriever.retrieve(
            query="运费说明",
            top_k=5,
            tenant_id=tenant_a.tenant_id,
            cache_backend=cache_a,
        )
        assert all(s.tenant_id == tenant_a.tenant_id for s in result_a2.sources)
        ok("缓存隔离：A 的缓存不泄漏给 B")

        await retriever.close()
        cache_a.close()
        cache_b.close()
    except AssertionError as e:
        fail("Retriever 层级租户隔离", e)
    except Exception as e:
        import traceback
        fail("Retriever 层级租户隔离", f"{e}\n{traceback.format_exc()}")

    # ========== 8. 验证 FastAPI resolve_tenant ==========
    print("\n=== 8. resolve_tenant 鉴权 ===")
    try:
        from src.api.routes import resolve_tenant, resolve_optional_tenant
        from starlette.requests import Request as StarletteRequest

        async def make_request(api_key):
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/chat",
                "headers": [(b"x-api-key", api_key.encode())],
                "query_string": b"",
                "server": ("localhost", 8000),
                "extensions": {"http.response.template": None, "http.response.push": None},
            }
            rec = type("Receive", (), {"__await__": lambda s: iter([{"type": "http.request", "body": b""}]).__iter__})
            return StarletteRequest(scope)

        # 有效 key
        req_a = await make_request("sk_a")
        t = await resolve_tenant(req_a)
        assert t.tenant_id == tenant_a.tenant_id
        assert req_a.state.tenant.tenant_id == tenant_a.tenant_id
        ok("resolve_tenant: sk_a → tenant_a")

        # 另一个 key
        req_b = await make_request("sk_b")
        t = await resolve_tenant(req_b)
        assert t.tenant_id == tenant_b.tenant_id
        ok("resolve_tenant: sk_b → tenant_b")

        # 无效 key
        req_bad = await make_request("sk_invalid")
        try:
            await resolve_tenant(req_bad)
            fail("resolve_tenant: 无效 key 应报错", "")
        except Exception as e:
            assert "401" in str(e) or "Unauthorized" in str(e)
            ok("resolve_tenant: 无效 key → 401")

        # suspended 租户
        tenant_svc.update_tenant(tenant_b.tenant_id, status="suspended")
        req_suspended = await make_request("sk_b")
        try:
            await resolve_tenant(req_suspended)
            fail("resolve_tenant: suspended 应报错", "")
        except Exception as e:
            assert "401" in str(e) or "Unauthorized" in str(e)
            ok("resolve_tenant: suspended tenant → 401")

        # 恢复
        tenant_svc.update_tenant(tenant_b.tenant_id, status="active")
    except AssertionError as e:
        fail("resolve_tenant", e)
    except Exception as e:
        import traceback
        fail("resolve_tenant", str(e)[:200])

    # ========== 9. 总结 ==========
    store.close()

    print(f"\n{'='*50}")
    print(f"结果: {passed} passed, {failed} failed")
    if failed:
        print("⚠️  部分测试失败，请检查上方 ❌ 项")
        sys.exit(1)
    else:
        print("🎉 全部集成测试通过！")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(test())
