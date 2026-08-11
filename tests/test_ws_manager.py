"""WebSocket 连接管理器测试。"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.ws_manager import ConnectionManager, get_manager


class MockWebSocket:
    """Mock WebSocket 用于单元测试。"""

    def __init__(self):
        self.sent = []
        self.closed = False
        self.close_code = None

    async def accept(self):
        pass

    async def send_json(self, data: dict):
        self.sent.append(data)

    async def close(self, code=1000, reason=""):
        self.closed = True
        self.close_code = code


class TestConnectionManager:
    """ConnectionManager 核心功能测试。"""

    @pytest.fixture
    def manager(self):
        m = ConnectionManager()
        yield m
        # 清理
        m._connections.clear()
        m._subscriptions.clear()
        m._project_agents.clear()
        m._ws_to_user.clear()
        m._heartbeats.clear()

    @pytest.mark.asyncio
    async def test_connect_agent(self, manager):
        """客服连接建立。"""
        ws = MockWebSocket()
        await manager.connect(ws, "agent_1", project_id="proj_1", is_agent=True)
        assert "agent_1" in manager._connections
        assert ws in manager._connections["agent_1"]
        assert "proj_1" in manager._project_agents
        assert "agent_1" in manager._project_agents["proj_1"]
        # 验证收到 connected 事件
        assert len(ws.sent) == 1
        assert ws.sent[0]["type"] == "connected"

    @pytest.mark.asyncio
    async def test_connect_widget(self, manager):
        """Widget 用户连接建立（非客服）。"""
        ws = MockWebSocket()
        await manager.connect(ws, "widget_proj_x", project_id="proj_x", is_agent=False)
        assert "widget_proj_x" in manager._connections
        # 非客服不注册到项目
        assert "proj_x" not in manager._project_agents

    @pytest.mark.asyncio
    async def test_disconnect(self, manager):
        """断开连接清理资源。"""
        ws = MockWebSocket()
        await manager.connect(ws, "agent_1", project_id="proj_1", is_agent=True)
        await manager.disconnect(ws)
        assert "agent_1" not in manager._connections
        assert "proj_1" not in manager._project_agents or "agent_1" not in manager._project_agents["proj_1"]

    @pytest.mark.asyncio
    async def test_send_to_user(self, manager):
        """向用户发送消息。"""
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        await manager.connect(ws1, "user_1")
        await manager.connect(ws2, "user_1")
        sent = await manager.send_to_user("user_1", {"type": "test", "data": "hello"})
        assert sent == 2
        assert len(ws1.sent) == 2  # 1 connect + 1 test
        assert ws1.sent[-1]["type"] == "test"
        assert len(ws2.sent) == 2

    @pytest.mark.asyncio
    async def test_send_to_user_offline(self, manager):
        """离线用户发送消息返回 0。"""
        sent = await manager.send_to_user("nonexistent", {"type": "test"})
        assert sent == 0

    @pytest.mark.asyncio
    async def test_broadcast_to_project(self, manager):
        """向项目广播。"""
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        await manager.connect(ws1, "agent_1", project_id="proj_1", is_agent=True)
        await manager.connect(ws2, "agent_2", project_id="proj_1", is_agent=True)
        sent = await manager.broadcast_to_project("proj_1", {"type": "broadcast", "data": "test"})
        assert sent == 2

    @pytest.mark.asyncio
    async def test_broadcast_exclude_user(self, manager):
        """广播排除指定用户。"""
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        await manager.connect(ws1, "agent_1", project_id="proj_1", is_agent=True)
        await manager.connect(ws2, "agent_2", project_id="proj_1", is_agent=True)
        sent = await manager.broadcast_to_project(
            "proj_1", {"type": "test"}, exclude_user="agent_1"
        )
        assert sent == 1

    @pytest.mark.asyncio
    async def test_subscribe(self, manager):
        """频道订阅。"""
        ws = MockWebSocket()
        await manager.connect(ws, "user_1")
        await manager.subscribe(ws, "conversation:conv_123")
        assert "conversation:conv_123" in manager._subscriptions["user_1"]

    @pytest.mark.asyncio
    async def test_unsubscribe(self, manager):
        """取消订阅。"""
        ws = MockWebSocket()
        await manager.connect(ws, "user_1")
        await manager.subscribe(ws, "conversation:conv_123")
        await manager.unsubscribe(ws, "conversation:conv_123")
        assert "conversation:conv_123" not in manager._subscriptions["user_1"]

    @pytest.mark.asyncio
    async def test_heartbeat_update(self, manager):
        """心跳更新。"""
        ws = MockWebSocket()
        await manager.connect(ws, "user_1")
        old = manager._heartbeats["user_1"]
        time.sleep(0.01)
        await manager.update_heartbeat(ws)
        assert manager._heartbeats["user_1"] >= old

    def test_is_connected(self, manager):
        """在线状态检查。"""
        assert manager.is_connected("nonexistent") is False

    @pytest.mark.asyncio
    async def test_is_connected_after_connect(self, manager):
        ws = MockWebSocket()
        await manager.connect(ws, "user_1")
        assert manager.is_connected("user_1") is True

    def test_get_online_agents(self, manager):
        """获取在线客服列表。"""
        assert manager.get_online_agents("proj_1") == []

    @pytest.mark.asyncio
    async def test_multiple_connections_same_user(self, manager):
        """同一用户多连接。"""
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        await manager.connect(ws1, "user_1")
        await manager.connect(ws2, "user_1")
        assert len(manager._connections["user_1"]) == 2
        assert manager._count_connections() == 2

    @pytest.mark.asyncio
    async def test_disconnect_partial(self, manager):
        """部分断开不影响其他连接。"""
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        await manager.connect(ws1, "user_1")
        await manager.connect(ws2, "user_1")
        await manager.disconnect(ws1)
        assert "user_1" in manager._connections
        assert len(manager._connections["user_1"]) == 1


class TestGetManager:
    """全局单例测试。"""

    def test_singleton(self):
        m1 = get_manager()
        m2 = get_manager()
        assert m1 is m2