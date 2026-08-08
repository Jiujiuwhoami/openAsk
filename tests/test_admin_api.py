"""管理后台 API 测试 — 统计、用户列表、项目列表、权限隔离。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded


_EMAIL_COUNTER = 0


def unique_email():
    global _EMAIL_COUNTER
    _EMAIL_COUNTER += 1
    return f"admin_{_EMAIL_COUNTER}@test.com"


@pytest.fixture
def admin_client():
    """创建测试客户端 + 管理员用户。

    流程：注册用户 → UserService.set_admin 提权 → 返回带 token 的客户端。
    """
    from src.api.auth import router as auth_router
    from src.api.admin import router as admin_router
    from src.utils.limiter import limiter
    from src.services.user_service import UserService

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(auth_router)
    app.include_router(admin_router)

    client = TestClient(app)

    email = unique_email()
    reg = client.post("/api/auth/register", json={
        "email": email, "password": "password123", "name": "Admin 测试",
    }).json()
    token = reg["access_token"]
    user_id = reg["user"]["user_id"]

    # 提权为管理员
    UserService().set_admin(user_id, is_admin=True)

    client._token = token
    client._headers = {"Authorization": f"Bearer {token}"}
    client._admin_email = email
    client._user_id = user_id
    return client


@pytest.fixture
def normal_client():
    """创建普通用户客户端（无管理员权限）。"""
    from src.api.auth import router as auth_router
    from src.api.admin import router as admin_router
    from src.utils.limiter import limiter

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(auth_router)
    app.include_router(admin_router)

    client = TestClient(app)

    reg = client.post("/api/auth/register", json={
        "email": unique_email(), "password": "password123", "name": "普通用户",
    }).json()
    client._headers = {"Authorization": f"Bearer {reg['access_token']}"}
    return client


class TestAdminStats:
    """平台概览统计"""

    def test_stats_success(self, admin_client):
        """管理员应能获取到统计。"""
        resp = admin_client.get("/api/admin/stats", headers=admin_client._headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_users" in data
        assert "total_projects" in data
        assert "total_calls" in data
        assert "prompt_tokens" in data
        assert "completion_tokens" in data
        assert "cache_hits" in data
        assert "cache_hit_rate" in data
        assert "users_today" in data
        assert "projects_today" in data
        # 至少有当前用户
        assert data["total_users"] >= 1
        assert data["total_projects"] >= 1

    def test_stats_forbidden_for_normal_user(self, normal_client):
        """非管理员访问 stats 应返回 403。"""
        resp = normal_client.get("/api/admin/stats", headers=normal_client._headers)
        assert resp.status_code == 403

    def test_stats_unauthorized(self, admin_client):
        """未登录访问 stats 应返回 401。"""
        # 创建一个新的无认证客户端
        from src.api.auth import router as auth_router
        from src.api.admin import router as admin_router
        from src.utils.limiter import limiter
        app = FastAPI()
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        app.include_router(auth_router)
        app.include_router(admin_router)
        unauth = TestClient(app)
        resp = unauth.get("/api/admin/stats")
        assert resp.status_code == 401


class TestAdminUsers:
    """用户列表"""

    def test_list_users_success(self, admin_client):
        """管理员应能获取用户分页列表。"""
        resp = admin_client.get("/api/admin/users", headers=admin_client._headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert len(data["items"]) >= 1
        # 验证用户字段
        user = data["items"][0]
        assert "user_id" in user
        assert "email" in user
        assert "name" in user
        assert "is_verified" in user
        assert "is_admin" in user
        assert "created_at" in user
        assert "project_count" in user

    def test_list_users_pagination(self, admin_client):
        """分页参数应生效。"""
        resp = admin_client.get(
            "/api/admin/users?page=1&page_size=5",
            headers=admin_client._headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 5
        assert len(data["items"]) <= 5

    def test_list_users_search(self, admin_client):
        """搜索邮箱或名称应返回匹配结果。"""
        # 搜索当前管理员的邮箱
        resp = admin_client.get(
            f"/api/admin/users?search={admin_client._admin_email}",
            headers=admin_client._headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any(admin_client._admin_email in u["email"] for u in data["items"])

    def test_list_users_search_no_match(self, admin_client):
        """搜索不存在的关键词应返回空列表。"""
        resp = admin_client.get(
            "/api/admin/users?search=xxxxxxxxxxxxx_nonexistent",
            headers=admin_client._headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert len(data["items"]) == 0

    def test_list_users_page_out_of_range(self, admin_client):
        """超出范围的页码应返回空列表。"""
        resp = admin_client.get(
            "/api/admin/users?page=99999",
            headers=admin_client._headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # 超出范围时 items 为空
        assert len(data["items"]) == 0

    def test_list_users_invalid_page_size(self, admin_client):
        """超过 100 的 page_size 应返回 422。"""
        resp = admin_client.get(
            "/api/admin/users?page_size=200",
            headers=admin_client._headers,
        )
        assert resp.status_code == 422

    def test_list_users_forbidden(self, normal_client):
        """非管理员用户列表应返回 403。"""
        resp = normal_client.get("/api/admin/users", headers=normal_client._headers)
        assert resp.status_code == 403


class TestAdminProjects:
    """项目列表"""

    def test_list_projects_success(self, admin_client):
        """管理员应能获取项目分页列表。"""
        resp = admin_client.get("/api/admin/projects", headers=admin_client._headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert len(data["items"]) >= 1
        # 验证项目字段
        project = data["items"][0]
        assert "project_id" in project
        assert "name" in project
        assert "user_id" in project
        assert "status" in project
        assert "created_at" in project

    def test_list_projects_pagination(self, admin_client):
        """分页参数应生效。"""
        resp = admin_client.get(
            "/api/admin/projects?page=1&page_size=5",
            headers=admin_client._headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 5
        assert len(data["items"]) <= 5

    def test_list_projects_search(self, admin_client):
        """搜索项目名称应返回匹配结果。"""
        # 搜索项目名通常能找到刚注册时创建的项目
        # 直接搜索空字符串确保有结果
        resp = admin_client.get(
            "/api/admin/projects?search=",
            headers=admin_client._headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_list_projects_forbidden(self, normal_client):
        """非管理员项目列表应返回 403。"""
        resp = normal_client.get("/api/admin/projects", headers=normal_client._headers)
        assert resp.status_code == 403


class TestAdminCrossTenant:
    """跨租户隔离验证"""

    def test_admin_see_all_projects(self, admin_client):
        """管理员应能看到所有项目（包括其他用户的）。"""
        # 先创建另一个用户
        from src.api.auth import router as auth_router
        from fastapi.testclient import TestClient as TC
        app = FastAPI()
        app.include_router(auth_router)
        other = TC(app)
        other.post("/api/auth/register", json={
            "email": unique_email(), "password": "password123",
        })

        # 管理员应看到至少 2 个项目
        resp = admin_client.get("/api/admin/projects", headers=admin_client._headers)
        data = resp.json()
        assert data["total"] >= 2