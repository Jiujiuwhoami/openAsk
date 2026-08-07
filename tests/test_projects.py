"""项目 API 测试 — 创建、列表、详情、更新、删除、轮换 Key、统计、嵌入脚本。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_EMAIL_COUNTER = 0


def unique_email():
    global _EMAIL_COUNTER
    _EMAIL_COUNTER += 1
    return f"proj_{_EMAIL_COUNTER}@test.com"


@pytest.fixture
def client():
    """创建测试客户端，先注册用户并获得 token。"""
    from src.api.auth import router as auth_router
    from src.api.projects import router as projects_router
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(projects_router)

    test_client = TestClient(app)

    email = unique_email()
    reg = test_client.post("/api/auth/register", json={
        "email": email, "password": "password123", "name": "项目测试",
    }).json()
    test_client._token = reg["access_token"]
    test_client._headers = {"Authorization": f"Bearer {test_client._token}"}
    test_client._project_id = reg["project"]["project_id"]
    return test_client


class TestListProjects:
    def test_list_projects(self, client):
        resp = client.get("/api/projects", headers=client._headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_list_projects_unauthorized(self, client):
        """不传 Authorization header 应返回 401。"""
        # 创建一个新的未认证客户端
        from src.api.auth import router as auth_router
        from src.api.projects import router as projects_router
        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(projects_router)
        unauth = TestClient(app)
        assert unauth.get("/api/projects").status_code == 401


class TestCreateProject:
    def test_create_project(self, client):
        resp = client.post("/api/projects", json={"name": "新项目"}, headers=client._headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "新项目"
        assert data["api_key"].startswith("sk_")

    def test_create_project_empty_name(self, client):
        resp = client.post("/api/projects", json={"name": ""}, headers=client._headers)
        assert resp.status_code == 422

    def test_create_project_unauthorized(self, client):
        from src.api.auth import router as auth_router
        from src.api.projects import router as projects_router
        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(projects_router)
        unauth = TestClient(app)
        assert unauth.post("/api/projects", json={"name": "新项目"}).status_code == 401


class TestGetProject:
    def test_get_project(self, client):
        resp = client.get(f"/api/projects/{client._project_id}", headers=client._headers)
        assert resp.status_code == 200
        assert resp.json()["project_id"] == client._project_id

    def test_get_project_not_found(self, client):
        resp = client.get("/api/projects/nonexistent", headers=client._headers)
        assert resp.status_code == 404


class TestUpdateProject:
    def test_update_project_name(self, client):
        resp = client.put(
            f"/api/projects/{client._project_id}",
            json={"name": "更新后的名称"},
            headers=client._headers,
        )
        assert resp.status_code == 200

    def test_update_project_llm_config(self, client):
        resp = client.put(
            f"/api/projects/{client._project_id}",
            json={"llm_api_base": "https://custom.api.com/v1", "llm_model": "gpt-4"},
            headers=client._headers,
        )
        assert resp.status_code == 200


class TestDeleteProject:
    def test_delete_project(self, client):
        created = client.post("/api/projects", json={"name": "待删除"}, headers=client._headers).json()
        resp = client.delete(f"/api/projects/{created['project_id']}", headers=client._headers)
        assert resp.status_code == 200

    def test_delete_project_not_found(self, client):
        resp = client.delete("/api/projects/nonexistent", headers=client._headers)
        assert resp.status_code == 404


class TestRotateKey:
    def test_rotate_key(self, client):
        created = client.post("/api/projects", json={"name": "Key轮换"}, headers=client._headers).json()
        resp = client.post(f"/api/projects/{created['project_id']}/rotate-key", headers=client._headers)
        assert resp.status_code == 200
        assert resp.json()["api_key"] != created["api_key"]


class TestProjectStats:
    def test_get_stats(self, client):
        resp = client.get(f"/api/projects/{client._project_id}/stats", headers=client._headers)
        assert resp.status_code == 200
        assert "total_calls" in resp.json()


class TestEmbedScript:
    def test_get_embed_script(self, client):
        """获取嵌入脚本。"""
        resp = client.get(f"/api/projects/{client._project_id}/embed-script", headers=client._headers)
        assert resp.status_code == 200
        assert "openask" in resp.json()["script"].lower()