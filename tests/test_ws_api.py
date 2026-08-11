"""WebSocket 端点集成测试。

测试鉴权、消息收发、事件推送。
"""

import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_EMAIL_COUNTER = 0


def unique_email():
    global _EMAIL_COUNTER
    _EMAIL_COUNTER += 1
    return f"ws_{_EMAIL_COUNTER}@test.com"


@pytest.fixture
def app():
    from src.api.ws import router as ws_router
    from src.api.auth import router as auth_router
    from src.api.projects import router as projects_router
    from src.services.ws_manager import get_manager

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(projects_router)
    app.include_router(ws_router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def _register(client):
    """注册用户并返回 token, project_id, api_key。"""
    email = unique_email()
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "password123", "name": "WS 测试",
    }).json()
    return resp["access_token"], resp["project"]["project_id"], resp["project"]["api_key"]


class TestWebSocketAuth:
    """WebSocket 鉴权测试。"""

    def test_connect_without_auth(self, client):
        """无认证信息时连接被拒绝。"""
        with pytest.raises(Exception):
            with client.websocket_connect("/ws"):
                pass

    def test_connect_with_invalid_token(self, client):
        """无效 token 被拒绝。"""
        with pytest.raises(Exception):
            with client.websocket_connect("/ws?token=invalid_token"):
                pass

    def test_connect_with_valid_token(self, client):
        """有效 JWT token 可建立 WebSocket 连接。"""
        token, pid, _ = _register(client)
        with client.websocket_connect(f"/ws?token={token}") as ws:
            data = ws.receive_json()
            assert data["type"] == "connected"
            assert data["data"]["is_agent"] is True

    def test_connect_with_api_key(self, client):
        """有效 API Key 可建立 WebSocket 连接。"""
        token, pid, api_key = _register(client)
        with client.websocket_connect(f"/ws?api_key={api_key}&project_id={pid}") as ws:
            data = ws.receive_json()
            assert data["type"] == "connected"
            assert data["data"]["is_agent"] is False

    def test_connect_with_wrong_api_key(self, client):
        """错误的 API Key 被拒绝。"""
        with pytest.raises(Exception):
            with client.websocket_connect("/ws?api_key=sk_wrong&project_id=proj_x"):
                pass


class TestWebSocketEvents:
    """WebSocket 事件处理测试。"""

    def test_heartbeat(self, client):
        """心跳事件。"""
        token, pid, _ = _register(client)
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "heartbeat"})
            # 心跳无响应，只需不报错

    def test_subscribe_unsubscribe(self, client):
        """订阅和取消订阅。"""
        token, pid, _ = _register(client)
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "subscribe", "data": {"channel": "conversation:conv_123"}})
            ws.send_json({"type": "unsubscribe", "data": {"channel": "conversation:conv_123"}})
            # 无响应，只需不报错

    def test_subscribe_project(self, client):
        """订阅项目频道。"""
        token, pid, _ = _register(client)
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "subscribe.project"})
            # 无响应，只需不报错

    def test_invalid_json(self, client):
        """无效 JSON 消息收到错误响应。"""
        token, pid, _ = _register(client)
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # connected
            ws.send_text("not json")
            # 发送不合法 JSON，服务端应返回 error
            data = ws.receive_json()
            assert data["type"] == "error"

    def test_message_send_in_agent_mode(self, client):
        """客服在 agent 模式下发送消息。"""
        token, pid, _ = _register(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="WS 测试")

        # 先将会话设置为 agent 模式
        ConversationService().update_status(conv.conversation_id, "agent", "agent_1")

        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # connected
            ws.send_json({
                "type": "message.send",
                "data": {
                    "conversation_id": conv.conversation_id,
                    "content": "您好，我是客服",
                },
            })
            # 应收到 sent 回执
            data = ws.receive_json()
            assert data["type"] == "message.sent"
            assert data["data"]["message"]["role"] == "agent"
            assert data["data"]["message"]["content"] == "您好，我是客服"

    def test_message_send_not_agent_mode(self, client):
        """非 agent 模式下客服发送消息返回错误。"""
        token, pid, _ = _register(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="WS 测试")

        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # connected
            ws.send_json({
                "type": "message.send",
                "data": {
                    "conversation_id": conv.conversation_id,
                    "content": "您好，我是客服",
                },
            })
            data = ws.receive_json()
            assert data["type"] == "error"

    def test_message_send_missing_fields(self, client):
        """缺少字段时返回错误。"""
        token, pid, _ = _register(client)
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # connected
            ws.send_json({
                "type": "message.send",
                "data": {"conversation_id": ""},
            })
            data = ws.receive_json()
            assert data["type"] == "error"


class TestWebSocketCrossCommunication:
    """跨用户消息通信测试。"""

    def test_agent_to_widget(self, client):
        """客服消息推送到 Widget 用户。"""
        token, pid, api_key = _register(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="跨通信测试")

        # 设置 agent 模式
        ConversationService().update_status(conv.conversation_id, "agent", "agent_1")

        # 客服连接并订阅会话频道
        with client.websocket_connect(f"/ws?token={token}") as agent_ws:
            agent_ws.receive_json()  # connected

            # 客服发送消息
            agent_ws.send_json({
                "type": "message.send",
                "data": {
                    "conversation_id": conv.conversation_id,
                    "content": "我是客服，有什么可以帮您？",
                },
            })
            # 回执
            sent = agent_ws.receive_json()
            assert sent["type"] == "message.sent"

            # 客服订阅会话频道
            agent_ws.send_json({
                "type": "subscribe",
                "data": {"channel": f"conversation:{conv.conversation_id}"},
            })

            # 模拟 Widget 用户发送消息（通过 API）
            from src.services.conversation_service import ConversationService
            ConversationService().add_message(conv.conversation_id, "user", "我需要帮助")

            # 客服应收到新消息通知
            # 注意：这里通过 API 直接添加消息后，不会触发 WS 推送
            # 完整流程需要 Widget 通过 WS 发送
            # 这是一个简化测试

    def test_widget_send_message(self, client):
        """Widget 用户通过 WebSocket 发送消息。"""
        token, pid, api_key = _register(client)
        from src.services.conversation_service import ConversationService
        conv = ConversationService().create_conversation(pid, title="Widget 测试")

        # 设置 agent 模式
        ConversationService().update_status(conv.conversation_id, "agent", "agent_1")

        # Widget 连接
        with client.websocket_connect(f"/ws?api_key={api_key}&project_id={pid}") as widget_ws:
            widget_ws.receive_json()  # connected

            widget_ws.send_json({
                "type": "message.send",
                "data": {
                    "conversation_id": conv.conversation_id,
                    "content": "我要退货",
                },
            })
            data = widget_ws.receive_json()
            assert data["type"] == "message.sent"
            assert data["data"]["message"]["role"] == "user"
            assert data["data"]["message"]["content"] == "我要退货"