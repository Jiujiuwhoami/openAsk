"""会话管理 API 测试 — 列表、详情、删除、更新标题。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_EMAIL_COUNTER = 0


def unique_email():
    global _EMAIL_COUNTER
    _EMAIL_COUNTER += 1
    return f"conv_{_EMAIL_COUNTER}@test.com"


@pytest.fixture
def client():
    from src.api.auth import router as auth_router
    from src.api.conversations import router as conv_router

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(conv_router)
    return TestClient(app)


def _register_and_login(client):
    """注册用户并返回 token 和 project_id。"""
    email = unique_email()
    reg = client.post("/api/auth/register", json={
        "email": email, "password": "password123", "name": "会话测试",
    }).json()
    return reg["access_token"], reg["project"]["project_id"]


class TestListConversations:
    def test_list_requires_auth(self, client):
        resp = client.get("/api/projects/proj_1/conversations")
        assert resp.status_code == 401

    def test_list_empty(self, client):
        token, pid = _register_and_login(client)
        resp = client.get(f"/api/projects/{pid}/conversations",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    def test_list_not_found_project(self, client):
        token, _ = _register_and_login(client)
        resp = client.get("/api/projects/nonexistent/conversations",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404


class TestGetConversation:
    def test_get_requires_auth(self, client):
        resp = client.get("/api/projects/proj_1/conversations/conv_1")
        assert resp.status_code == 401

    def test_get_not_found(self, client):
        token, pid = _register_and_login(client)
        resp = client.get(f"/api/projects/{pid}/conversations/conv_404",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404


class TestDeleteConversation:
    def test_delete_requires_auth(self, client):
        resp = client.delete("/api/projects/proj_1/conversations/conv_1")
        assert resp.status_code == 401

    def test_delete_not_found(self, client):
        token, pid = _register_and_login(client)
        resp = client.delete(f"/api/projects/{pid}/conversations/conv_404",
                             headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404


class TestUpdateTitle:
    def test_update_requires_auth(self, client):
        resp = client.put("/api/projects/proj_1/conversations/conv_1", json={"title": "新标题"})
        assert resp.status_code == 401

    def test_update_not_found(self, client):
        token, pid = _register_and_login(client)
        resp = client.put(f"/api/projects/{pid}/conversations/conv_404",
                          json={"title": "新标题"},
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404