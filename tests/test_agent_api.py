"""客服状态 API 测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_EMAIL_COUNTER = 0


def unique_email():
    global _EMAIL_COUNTER
    _EMAIL_COUNTER += 1
    return f"agent_api_{_EMAIL_COUNTER}@test.com"


@pytest.fixture
def client():
    from src.api.auth import router as auth_router
    from src.api.agent import router as agent_router

    app = FastAPI()
    app.state.limiter = None
    app.include_router(auth_router)
    app.include_router(agent_router)
    return TestClient(app)


def _register(client):
    email = unique_email()
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "password123", "name": "Agent 测试",
    }).json()
    return resp["access_token"], resp["project"]["project_id"]


class TestAgentStatusAPI:
    def test_requires_auth(self, client):
        resp = client.put("/api/agent/status", json={"status": "online"})
        assert resp.status_code == 401

    def test_set_status(self, client):
        token, pid = _register(client)
        resp = client.put("/api/agent/status", json={"status": "online"},
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["status"] == "online"

    def test_get_status(self, client):
        token, pid = _register(client)
        # 先设置
        client.put("/api/agent/status", json={"status": "busy"},
                   headers={"Authorization": f"Bearer {token}"})
        # 再获取
        resp = client.get("/api/agent/status",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "busy"

    def test_get_status_default(self, client):
        token, pid = _register(client)
        resp = client.get("/api/agent/status",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "offline"

    def test_invalid_status(self, client):
        token, pid = _register(client)
        resp = client.put("/api/agent/status", json={"status": "invalid"},
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 422

    def test_list_agents(self, client):
        token, pid = _register(client)
        # 设置状态
        client.put("/api/agent/status", json={"status": "online"},
                   headers={"Authorization": f"Bearer {token}"})
        resp = client.get(f"/api/projects/{pid}/agents",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_list_online_agents(self, client):
        token, pid = _register(client)
        client.put("/api/agent/status", json={"status": "online"},
                   headers={"Authorization": f"Bearer {token}"})
        resp = client.get(f"/api/projects/{pid}/agents?online_only=true",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1