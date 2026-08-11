"""话术库 API 测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_EMAIL_COUNTER = 0


def unique_email():
    global _EMAIL_COUNTER
    _EMAIL_COUNTER += 1
    return f"canned_api_{_EMAIL_COUNTER}@test.com"


@pytest.fixture
def client():
    from src.api.auth import router as auth_router
    from src.api.canned_responses import router as canned_router

    app = FastAPI()
    app.state.limiter = None
    app.include_router(auth_router)
    app.include_router(canned_router)
    return TestClient(app)


def _register(client):
    email = unique_email()
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "password123", "name": "话术测试",
    }).json()
    return resp["access_token"], resp["project"]["project_id"]


class TestCannedAPI:
    def test_create_requires_auth(self, client):
        resp = client.post("/api/projects/proj_1/canned-responses", json={
            "title": "欢迎语", "content": "您好！",
        })
        assert resp.status_code == 401

    def test_create_and_list(self, client):
        token, pid = _register(client)
        # 创建
        resp = client.post(f"/api/projects/{pid}/canned-responses", json={
            "title": "欢迎语", "content": "您好，欢迎咨询！", "category": "问候",
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # 列表
        resp = client.get(f"/api/projects/{pid}/canned-responses",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_list_filter_by_category(self, client):
        token, pid = _register(client)
        client.post(f"/api/projects/{pid}/canned-responses", json={
            "title": "欢迎语", "content": "您好！", "category": "问候",
        }, headers={"Authorization": f"Bearer {token}"})
        client.post(f"/api/projects/{pid}/canned-responses", json={
            "title": "物流查询", "content": "您的订单", "category": "物流",
        }, headers={"Authorization": f"Bearer {token}"})

        resp = client.get(f"/api/projects/{pid}/canned-responses?category=问候",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.json()["total"] == 1

    def test_update(self, client):
        token, pid = _register(client)
        create = client.post(f"/api/projects/{pid}/canned-responses", json={
            "title": "旧标题", "content": "旧内容",
        }, headers={"Authorization": f"Bearer {token}"}).json()
        cid = create["item"]["id"]

        resp = client.put(f"/api/projects/{pid}/canned-responses/{cid}", json={
            "title": "新标题", "content": "新内容",
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["item"]["title"] == "新标题"

    def test_delete(self, client):
        token, pid = _register(client)
        create = client.post(f"/api/projects/{pid}/canned-responses", json={
            "title": "删除测试", "content": "内容",
        }, headers={"Authorization": f"Bearer {token}"}).json()
        cid = create["item"]["id"]

        resp = client.delete(f"/api/projects/{pid}/canned-responses/{cid}",
                             headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_categories(self, client):
        token, pid = _register(client)
        client.post(f"/api/projects/{pid}/canned-responses", json={
            "title": "欢迎语", "content": "您好！", "category": "问候",
        }, headers={"Authorization": f"Bearer {token}"})

        resp = client.get(f"/api/projects/{pid}/canned-responses/categories",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert "问候" in resp.json()["items"]