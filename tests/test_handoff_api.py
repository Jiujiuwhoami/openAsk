"""人工客服转接 API 测试 — 提交转接、取消转接、排队位置、状态变更。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded


_EMAIL_COUNTER = 0


def unique_email():
    global _EMAIL_COUNTER
    _EMAIL_COUNTER += 1
    return f"handoff_api_{_EMAIL_COUNTER}@test.com"


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
    """注册用户并返回 token, project_id, api_key。"""
    email = unique_email()
    resp = client.post("/api/auth/register", json={
        "email": email,
        "password": "password123",
        "name": "Handoff 测试",
    }).json()
    return (
        resp["access_token"],
        resp["project"]["project_id"],
        resp["project"]["api_key"],
    )


class TestHandoffAPI:
    """用户主动转接 API 测试。"""

    def test_handoff_requires_api_key(self, client):
        """缺少 API Key 时返回 401。"""
        resp = client.post("/api/projects/proj_x/handoff", json={
            "query": "需要帮助",
        })
        assert resp.status_code == 401

    def test_handoff_wrong_api_key(self, client):
        """错误的 API Key 返回 401。"""
        resp = client.post("/api/projects/proj_x/handoff", json={
            "query": "需要帮助",
        }, headers={"X-API-Key": "sk_wrong"})
        assert resp.status_code == 401

    def test_handoff_success(self, client):
        """提交转接请求成功，返回请求 ID 和排队信息。"""
        _, pid, api_key = _register(client)

        # 先创建会话
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="测试")

        # 用正确的 project_id 和存在的 conversation_id 提交转接
        resp = client.post(f"/api/projects/{pid}/handoff", json={
            "conversation_id": conv.conversation_id,
            "query": "我需要人工帮助",
            "reason": "user_initiated",
            "priority": 0,
        }, headers={"X-API-Key": api_key})
        data = resp.json()
        assert resp.status_code == 200, f"handoff failed: {data}"
        assert "request_id" in data
        assert data["request_id"] > 0
        assert "queue_position" in data
        assert "estimated_wait_seconds" in data
        assert data["status"] in ("queuing", "agent")  # 可能自动分配

    def test_handoff_with_reason_system_suggested(self, client):
        """支持 system_suggested 转接原因。"""
        _, pid, api_key = _register(client)
        resp = client.post(f"/api/projects/{pid}/handoff", json={
            "query": "系统建议转接",
            "reason": "system_suggested",
        }, headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        assert resp.json()["request_id"] > 0

    def test_handoff_with_priority(self, client):
        """支持高优先级转接。"""
        _, pid, api_key = _register(client)
        resp = client.post(f"/api/projects/{pid}/handoff", json={
            "query": "紧急问题！",
            "reason": "user_initiated",
            "priority": 2,
        }, headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        assert resp.json()["request_id"] > 0

    def test_handoff_conversation_queuing_status(self, client):
        """提交转接后会话状态变为 queuing。"""
        _, pid, api_key = _register(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="测试转接状态")

        # 提交转接
        client.post(f"/api/projects/{pid}/handoff", json={
            "conversation_id": conv.conversation_id,
            "query": "转接状态测试",
        }, headers={"X-API-Key": api_key})

        # 验证会话状态
        conv_after = ConversationService().get_conversation(conv.conversation_id)
        assert conv_after is not None
        assert conv_after.status == "queuing"

    def test_handoff_queue_position(self, client):
        """转接请求排队位置正确。"""
        _, pid, api_key = _register(client)
        from src.services.conversation_service import ConversationService

        # 提交多个转接（不同会话）
        for i in range(3):
            conv = ConversationService().create_conversation(pid, title=f"排队测试{i}")
            client.post(f"/api/projects/{pid}/handoff", json={
                "conversation_id": conv.conversation_id,
                "query": f"排队问题{i}",
            }, headers={"X-API-Key": api_key})

        # 第三个请求应该排在 2 位
        conv3 = ConversationService().create_conversation(pid, title="排队测试3")
        resp = client.post(f"/api/projects/{pid}/handoff", json={
            "conversation_id": conv3.conversation_id,
            "query": "排队问题3",
        }, headers={"X-API-Key": api_key})
        data = resp.json()
        assert data["queue_position"] == 3
        assert data["estimated_wait_seconds"] == 180

    def test_handoff_cancel_success(self, client):
        """取消转接请求成功，会话恢复为 active。"""
        _, pid, api_key = _register(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="取消测试")

        # 提交转接
        client.post(f"/api/projects/{pid}/handoff", json={
            "conversation_id": conv.conversation_id,
            "query": "取消测试",
        }, headers={"X-API-Key": api_key})

        # 验证状态为 queuing
        assert ConversationService().get_conversation(conv.conversation_id).status == "queuing"

        # 取消转接
        cancel_resp = client.post(f"/api/projects/{pid}/handoff/cancel", json={
            "conversation_id": conv.conversation_id,
            "query": "",
        }, headers={"X-API-Key": api_key})
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["success"] is True
        assert cancel_resp.json()["status"] == "active"

        # 验证会话恢复为 active
        assert ConversationService().get_conversation(conv.conversation_id).status == "active"

    def test_handoff_cancel_without_conv_id(self, client):
        """取消转接时必须提供 conversation_id。"""
        _, pid, api_key = _register(client)
        resp = client.post(f"/api/projects/{pid}/handoff/cancel", json={
            "conversation_id": "",
            "query": "",
        }, headers={"X-API-Key": api_key})
        assert resp.status_code == 400

    def test_handoff_with_contact_info(self, client):
        """提交转接附带联系方式。"""
        _, pid, api_key = _register(client)
        resp = client.post(f"/api/projects/{pid}/handoff", json={
            "query": "需要帮助",
            "contact_email": "user@example.com",
            "contact_phone": "13800138000",
            "note": "请尽快处理",
        }, headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        assert resp.json()["request_id"] > 0

    def test_handoff_wrong_project(self, client):
        """使用项目 A 的 API Key 不能为项目 B 提交转接。"""
        _, pid_a, api_key_a = _register(client)
        # 用项目 A 的 key 去请求项目 B 的路径
        resp = client.post(f"/api/projects/some_other_project/handoff", json={
            "query": "跨项目测试",
        }, headers={"X-API-Key": api_key_a})
        assert resp.status_code == 403