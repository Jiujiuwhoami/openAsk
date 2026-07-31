#!/usr/bin/env python3
"""多租户改造 — 端到端集成测试（针对运行中的 FastAPI 服务）。

测试目标（补齐缺失测试 1/8/10）：
  1. /api/health 免鉴权
  8. 管理员 API（/api/admin/tenants）CRUD + 鉴权

用法（在 Docker 容器内或宿主机执行）：
  python scripts/integration_test_admin_api.py

前置条件：FastAPI 服务在 http://localhost:8000 运行，
          .env 中已配置 API_API_KEY 作为 admin key。
"""

import os
import sys
import json
import requests as httpx

BASE_URL = os.environ.get("OPENASK_URL", "http://localhost:8000")
ADMIN_KEY = os.environ.get("OPENASK_ADMIN_KEY", "")

passed = 0
failed = 0


def ok(name):
    global passed
    passed += 1
    print(f"  ✅ {name}")


def fail(name, detail=""):
    global failed
    failed += 1
    print(f"  ❌ {name}: {detail}")


def _headers(admin=False):
    h = {}
    if admin and ADMIN_KEY:
        h["X-API-Key"] = ADMIN_KEY
    return h


def test_health_no_auth():
    """测试 8: /api/health 免鉴权。"""
    try:
        r = httpx.get(f"{BASE_URL}/api/health")
        assert r.status_code == 200, f"HTTP {r.status_code}"
        data = r.json()
        assert data["status"] in ("healthy", "degraded"), data["status"]
        assert "version" in data
        assert "zvec_status" in data
        ok("/api/health 免鉴权 → 200, status=healthy")
    except Exception as e:
        fail("/api/health 免鉴权", str(e))


def test_admin_list_tenants():
    """测试 1: 管理员列出租户。"""
    try:
        r = httpx.get(f"{BASE_URL}/api/admin/tenants", headers=_headers(admin=True))
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
        data = r.json()
        assert isinstance(data, list)
        # 应包含 default 租户
        ids = [t["tenant_id"] for t in data]
        assert "default" in ids, f"default 不在列表: {ids}"
        ok(f"列出 {len(data)} 个租户 (含 default)")
    except Exception as e:
        fail("管理员列出租户", str(e))


def test_admin_unauthorized():
    """测试 1: 无 admin key → 401。"""
    try:
        r = httpx.get(f"{BASE_URL}/api/admin/tenants", headers={})
        assert r.status_code == 401, f"应返回 401，实际 {r.status_code}"
        ok("无 admin key → 401")
    except Exception as e:
        fail("管理员鉴权 401", str(e))


def test_admin_wrong_key():
    """测试 1: 错误 admin key → 401。"""
    try:
        r = httpx.get(
            f"{BASE_URL}/api/admin/tenants",
            headers={"X-API-Key": "wrong_key"},
        )
        assert r.status_code == 401, f"应返回 401，实际 {r.status_code}"
        ok("错误 admin key → 401")
    except Exception as e:
        fail("管理员错误 key 401", str(e))


def test_admin_create_and_delete_tenant():
    """测试 1: 创建 → 查询 → 删除（软删除）。"""
    try:
        test_name = "e2e_test_tenant"

        # 创建
        r = httpx.post(
            f"{BASE_URL}/api/admin/tenants",
            headers=_headers(admin=True),
            json={"name": test_name},
        )
        assert r.status_code == 200, f"创建失败 HTTP {r.status_code}: {r.text[:200]}"
        t = r.json()
        assert t["name"] == test_name
        assert t["status"] == "active"
        assert t["api_key"].startswith("sk_")
        tenant_id = t["tenant_id"]

        # 查询
        r = httpx.get(
            f"{BASE_URL}/api/admin/tenants/{tenant_id}",
            headers=_headers(admin=True),
        )
        assert r.status_code == 200
        assert r.json()["tenant_id"] == tenant_id
        ok(f"创建 → 查询 tenant={tenant_id}")

        # 删除（软删除）
        r = httpx.delete(
            f"{BASE_URL}/api/admin/tenants/{tenant_id}",
            headers=_headers(admin=True),
        )
        assert r.status_code == 200
        assert r.json()["success"] is True
        ok(f"删除（软删除）tenant={tenant_id}")

        # 再次查询 → 状态为 deleted
        r = httpx.get(
            f"{BASE_URL}/api/admin/tenants/{tenant_id}",
            headers=_headers(admin=True),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "deleted"
        ok("删除后状态为 deleted")

    except Exception as e:
        fail("创建/删除租户", str(e))


def test_admin_rotate_key():
    """测试 1: 轮换 API Key。"""
    try:
        # 用 default 租户
        r = httpx.post(
            f"{BASE_URL}/api/admin/tenants/default/rotate-key",
            headers=_headers(admin=True),
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
        new_key = r.json()["api_key"]
        assert new_key.startswith("sk_")
        ok(f"轮换 default 租户 API Key → {new_key[:10]}...")

        # 旧 key 失效（health 不再用旧 key，改为验证 get_by_api_key）
        # 这里仅验证返回了新 key，旧 key 测试在 component 测试中

    except Exception as e:
        fail("轮换 API Key", str(e))


def test_admin_nonexistent_tenant():
    """测试 1: 查询不存在的租户 → 404。"""
    try:
        r = httpx.get(
            f"{BASE_URL}/api/admin/tenants/nonexistent_tenant_xyz",
            headers=_headers(admin=True),
        )
        assert r.status_code == 404, f"应返回 404，实际 {r.status_code}"
        ok("查询不存在的租户 → 404")
    except Exception as e:
        fail("查询不存在租户 404", str(e))


def test_admin_get_stats():
    """测试 1: 获取租户统计。"""
    try:
        r = httpx.get(
            f"{BASE_URL}/api/admin/tenants/default/stats",
            headers=_headers(admin=True),
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
        data = r.json()
        assert data["tenant_id"] == "default"
        assert "document_count" in data
        assert "total_calls" in data
        ok(f"租户统计: {json.dumps(data)}")
    except Exception as e:
        fail("获取租户统计", str(e))


def test_admin_update_tenant():
    """测试 1: 更新租户（system_prompt 定制）。"""
    try:
        # 创建一个测试租户
        r = httpx.post(
            f"{BASE_URL}/api/admin/tenants",
            headers=_headers(admin=True),
            json={"name": "e2e_update_test", "system_prompt": "你是一个客服"},
        )
        assert r.status_code == 200
        t = r.json()
        tenant_id = t["tenant_id"]

        # 更新
        r = httpx.put(
            f"{BASE_URL}/api/admin/tenants/{tenant_id}",
            headers=_headers(admin=True),
            json={"system_prompt": "你是一个专业的技术支持助手"},
        )
        assert r.status_code == 200
        updated = r.json()
        assert updated["system_prompt"] == "你是一个专业的技术支持助手"
        ok(f"更新 system_prompt 成功")

        # 清理
        httpx.delete(
            f"{BASE_URL}/api/admin/tenants/{tenant_id}",
            headers=_headers(admin=True),
        )
        ok("清理测试租户")

    except Exception as e:
        fail("更新租户", str(e))


def main():
    global passed, failed, ADMIN_KEY

    # 检查 admin key
    if not ADMIN_KEY:
        print("⚠️  OPENASK_ADMIN_KEY 未设置，使用 .env 中的 API_API_KEY")
        # 尝试从 .env 读取
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("API_API_KEY="):
                        ADMIN_KEY = line.split("=", 1)[1].strip().strip("\"'")
                        break
                    elif line.startswith("API_KEY=") and not ADMIN_KEY:
                        ADMIN_KEY = line.split("=", 1)[1].strip().strip("\"'")
                        break
        if not ADMIN_KEY:
            print("❌ 无法获取 ADMIN_KEY，跳过需要 admin 鉴权的测试")
            # 仍运行 health 测试
            test_health_no_auth()
            print(f"\n结果: {passed} passed, {failed} failed (部分测试跳过)")
            sys.exit(0)

    print(f"多租户 E2E 集成测试 — 服务: {BASE_URL}")
    print(f"{'='*50}")

    test_health_no_auth()
    test_admin_unauthorized()
    test_admin_wrong_key()
    test_admin_list_tenants()
    test_admin_create_and_delete_tenant()
    test_admin_rotate_key()
    test_admin_nonexistent_tenant()
    test_admin_get_stats()
    test_admin_update_tenant()

    print(f"\n{'='*50}")
    print(f"结果: {passed} passed, {failed} failed")
    if failed:
        print("⚠️  部分测试失败")
        sys.exit(1)
    else:
        print("🎉 全部 E2E 集成测试通过！")
        sys.exit(0)


if __name__ == "__main__":
    main()
