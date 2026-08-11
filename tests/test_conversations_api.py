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


class TestTransferConversation:
    """客服间转接测试。"""

    def test_transfer_requires_auth(self, client):
        resp = client.post("/api/projects/proj_1/conversations/conv_1/transfer",
                           json={"target_agent_id": "u_xxx"})
        assert resp.status_code == 401

    def test_transfer_not_agent_status(self, client):
        """非 agent 状态无法转接。"""
        token, pid = _register_and_login(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="转接测试")

        resp = client.post(f"/api/projects/{pid}/conversations/{conv.conversation_id}/transfer",
                           json={"target_agent_id": "u_other"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 409

    def test_transfer_success(self, client):
        """转接成功，agent_id 更新。"""
        token, pid = _register_and_login(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="转接测试")
        ConversationService().update_status(conv.conversation_id, "agent", "agent_old")

        resp = client.post(f"/api/projects/{pid}/conversations/{conv.conversation_id}/transfer",
                           json={"target_agent_id": "agent_new"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["to_agent"] == "agent_new"

        # 验证会话 agent_id 已更新
        conv_after = ConversationService().get_conversation(conv.conversation_id)
        assert conv_after.agent_id == "agent_new"
        assert conv_after.status == "agent"

    def test_transfer_to_self(self, client):
        """不能转接给自己。"""
        token, pid = _register_and_login(client)
        from src.services.conversation_service import ConversationService
        from src.utils.config import settings
        import jwt
        conv = ConversationService().create_conversation(pid, title="转接测试")

        # 从 token 中获取 user_id
        payload = jwt.decode(token, settings.auth.secret_key, algorithms=[settings.auth.algorithm])
        user_id = payload["sub"]

        ConversationService().update_status(conv.conversation_id, "agent", user_id)
        resp = client.post(f"/api/projects/{pid}/conversations/{conv.conversation_id}/transfer",
                           json={"target_agent_id": user_id},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400

    def test_add_tag(self, client):
        """添加会话标签。"""
        token, pid = _register_and_login(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="标签测试")

        resp = client.post(f"/api/projects/{pid}/conversations/{conv.conversation_id}/tags",
                           json={"tag": "投诉"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert "投诉" in resp.json()["tags"]

    def test_get_tags(self, client):
        token, pid = _register_and_login(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="标签测试")
        ConversationService().add_tag(conv.conversation_id, "咨询")

        resp = client.get(f"/api/projects/{pid}/conversations/{conv.conversation_id}/tags",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["咨询"]

    def test_tags_overview(self, client):
        """标签概览。"""
        token, pid = _register_and_login(client)
        from src.services.conversation_service import ConversationService
        cs = ConversationService()
        conv1 = cs.create_conversation(pid, title="标签A")
        conv2 = cs.create_conversation(pid, title="标签B")
        cs.add_tag(conv1.conversation_id, "投诉")
        cs.add_tag(conv2.conversation_id, "投诉")
        cs.add_tag(conv2.conversation_id, "物流")

        resp = client.get(f"/api/projects/{pid}/conversations/tags/overview",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) >= 2
        tags = {i["tag"]: i["count"] for i in data["items"]}
        assert tags["投诉"] == 2
        assert tags["物流"] == 1

    def test_remove_tag(self, client):
        token, pid = _register_and_login(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="标签测试")
        ConversationService().add_tag(conv.conversation_id, "投诉")

        resp = client.delete(f"/api/projects/{pid}/conversations/{conv.conversation_id}/tags?tag=投诉",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["tags"] == []

    def test_transfer_adds_system_message(self, client):
        """转接时添加系统通知消息。"""
        token, pid = _register_and_login(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="转接测试")
        ConversationService().update_status(conv.conversation_id, "agent", "agent_old")

        client.post(f"/api/projects/{pid}/conversations/{conv.conversation_id}/transfer",
                    json={"target_agent_id": "agent_new", "reason": "专业问题"},
                    headers={"Authorization": f"Bearer {token}"})

        messages = ConversationService().get_messages_by_conversation(conv.conversation_id)
        assert messages["total"] >= 1
        last = messages["items"][-1]
        assert last["role"] == "system"
        assert "转接" in last["content"]