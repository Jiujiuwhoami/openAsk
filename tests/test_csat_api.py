"""CSAT 满意度评价 API 测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_EMAIL_COUNTER = 0


def unique_email():
    global _EMAIL_COUNTER
    _EMAIL_COUNTER += 1
    return f"csat_{_EMAIL_COUNTER}@test.com"


@pytest.fixture
def client():
    from src.api.auth import router as auth_router
    from src.api.analytics import router as analytics_router

    app = FastAPI()
    app.state.limiter = None
    app.include_router(auth_router)
    app.include_router(analytics_router)
    return TestClient(app)


def _register(client):
    email = unique_email()
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "password123", "name": "CSAT 测试",
    }).json()
    return resp["access_token"], resp["project"]["project_id"], resp["project"]["api_key"]


class TestCsatAPI:
    """CSAT 满意度评价 API 测试。"""

    def test_submit_csat_requires_api_key(self, client):
        resp = client.post("/api/feedback/csat", json={"rating": 5, "conversation_id": "conv_1"})
        assert resp.status_code == 401

    def test_submit_csat_success(self, client):
        _, pid, api_key = _register(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="CSAT 测试")

        resp = client.post("/api/feedback/csat", json={
            "conversation_id": conv.conversation_id,
            "rating": 5,
            "tags": ["解决了我问题", "回复很快"],
            "feedback": "非常满意",
        }, headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["request_id"] > 0
        assert "感谢" in data["message"]

    def test_submit_csat_invalid_rating(self, client):
        _, pid, api_key = _register(client)
        resp = client.post("/api/feedback/csat", json={
            "conversation_id": "conv_1", "rating": 6,
        }, headers={"X-API-Key": api_key})
        assert resp.status_code == 422

    def test_csat_stats_requires_auth(self, client):
        resp = client.get("/api/projects/proj_1/analytics/csat")
        assert resp.status_code == 401

    def test_csat_stats(self, client):
        token, pid, api_key = _register(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="CSAT 统计测试")

        # 先提交评价
        client.post("/api/feedback/csat", json={
            "conversation_id": conv.conversation_id, "rating": 5,
        }, headers={"X-API-Key": api_key})

        # 获取统计
        resp = client.get(f"/api/projects/{pid}/analytics/csat",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert data["avg_rating"] > 0

    def test_csat_list(self, client):
        token, pid, api_key = _register(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="CSAT 列表测试")

        client.post("/api/feedback/csat", json={
            "conversation_id": conv.conversation_id, "rating": 4, "feedback": "不错",
        }, headers={"X-API-Key": api_key})

        resp = client.get(f"/api/projects/{pid}/analytics/csat/list",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1
        assert resp.json()["items"][0]["rating"] == 4


class TestAgentPerformanceAPI:
    """客服绩效 API 测试。"""

    def test_requires_auth(self, client):
        resp = client.get("/api/projects/proj_1/analytics/agents")
        assert resp.status_code == 401

    def test_agent_performance(self, client):
        token, pid, api_key = _register(client)
        from src.services.conversation_service import ConversationService
        from src.services.analytics_service import AnalyticsService

        # 创建会话并指派给客服
        conv = ConversationService().create_conversation(pid, title="绩效测试")
        ConversationService().update_status(conv.conversation_id, "agent", "agent_perf")
        ConversationService().add_message(conv.conversation_id, "agent", "回复内容")

        # 记录 CSAT
        AnalyticsService().record_csat(conv.conversation_id, pid, 5, agent_id="agent_perf")

        resp = client.get(f"/api/projects/{pid}/analytics/agents",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        agent = next((a for a in data["items"] if a["agent_id"] == "agent_perf"), None)
        assert agent is not None
        assert agent["conversations"] >= 1
        assert agent["messages_sent"] >= 1
        assert agent["csat_total"] >= 1