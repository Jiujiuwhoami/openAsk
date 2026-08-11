"""WebSocket 连接管理器。

管理用户连接生命周期、频道订阅、消息路由。
支持按用户推送、按项目广播、心跳检测。

使用方式：
    manager = ConnectionManager()
    await manager.connect(ws, user_id)
    await manager.send_to_user(user_id, {"type": "message.new", "data": ...})
    await manager.broadcast_to_project(project_id, {"type": "handoff.new", "data": ...})
"""

import asyncio
import json
import time
import logging
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 心跳超时（秒）
HEARTBEAT_TIMEOUT = 60
# 心跳间隔（秒）
HEARTBEAT_INTERVAL = 30


class ConnectionManager:
    """WebSocket 连接管理器。

    管理三层映射：
    - user_id → Set[WebSocket]    用户的所有连接
    - user_id → Set[str]          用户订阅的频道
    - project_id → Set[user_id]   项目下的在线客服
    """

    def __init__(self):
        # user_id → {WebSocket, ...}
        self._connections: Dict[str, Set[WebSocket]] = defaultdict(set)
        # user_id → {channel, ...}
        self._subscriptions: Dict[str, Set[str]] = defaultdict(set)
        # project_id → {user_id, ...}
        self._project_agents: Dict[str, Set[str]] = defaultdict(set)
        # WebSocket → user_id (反向查找 for disconnect)
        self._ws_to_user: Dict[WebSocket, str] = {}
        # user_id → last_heartbeat timestamp
        self._heartbeats: Dict[str, float] = {}
        # 锁
        self._lock = asyncio.Lock()
        # 心跳检查任务
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """启动心跳检查循环。"""
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("WebSocket ConnectionManager 已启动")

    async def stop(self):
        """停止心跳检查并关闭所有连接。"""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        # 关闭所有连接
        all_ws = set()
        async with self._lock:
            for ws_list in self._connections.values():
                all_ws.update(ws_list)
        for ws in all_ws:
            try:
                await ws.close(code=1001, reason="服务关闭")
            except Exception:
                pass
        logger.info(f"WebSocket ConnectionManager 已停止，关闭 {len(all_ws)} 个连接")

    async def connect(
        self,
        ws: WebSocket,
        user_id: str,
        project_id: str = "",
        is_agent: bool = False,
    ) -> None:
        """接受 WebSocket 连接并注册。

        Args:
            ws: WebSocket 实例
            user_id: 用户 ID
            project_id: 项目 ID（用于客服广播）
            is_agent: 是否为客服用户
        """
        await ws.accept()
        async with self._lock:
            self._connections[user_id].add(ws)
            self._ws_to_user[ws] = user_id
            self._heartbeats[user_id] = time.time()
            if is_agent and project_id:
                self._project_agents[project_id].add(user_id)

        logger.info(
            f"WebSocket 连接: user={user_id[:12]}..., "
            f"project={project_id[:12] if project_id else 'N/A'}, "
            f"agent={is_agent}, 当前连接数={self._count_connections()}"
        )

        # 发送连接成功事件
        await self._send_json(ws, {
            "type": "connected",
            "data": {
                "user_id": user_id,
                "project_id": project_id,
                "is_agent": is_agent,
            },
        })

    async def disconnect(self, ws: WebSocket) -> None:
        """断开连接并清理资源。"""
        user_id = self._ws_to_user.get(ws)
        if not user_id:
            return

        async with self._lock:
            self._connections[user_id].discard(ws)
            if not self._connections[user_id]:
                del self._connections[user_id]
                self._heartbeats.pop(user_id, None)
                # 清理所有项目中的客服标记
                for project_agents in self._project_agents.values():
                    project_agents.discard(user_id)
                # 清理订阅
                self._subscriptions.pop(user_id, None)
            self._ws_to_user.pop(ws, None)

        logger.info(
            f"WebSocket 断开: user={user_id[:12] if user_id else 'unknown'}..., "
            f"剩余连接数={self._count_connections()}"
        )

    async def send_to_user(self, user_id: str, event: dict) -> int:
        """向用户的所有连接发送消息。返回发送成功的连接数。"""
        async with self._lock:
            connections = set(self._connections.get(user_id, set()))

        sent = 0
        for ws in connections:
            try:
                await self._send_json(ws, event)
                sent += 1
            except Exception as e:
                logger.warning(f"发送消息失败: user={user_id[:12]}, err={e}")
                await self.disconnect(ws)
        return sent

    async def broadcast_to_project(
        self, project_id: str, event: dict, exclude_user: str = ""
    ) -> int:
        """向项目的所有在线客服广播（可选排除指定用户）。返回发送数。"""
        async with self._lock:
            agent_ids = set(self._project_agents.get(project_id, set()))

        if exclude_user:
            agent_ids.discard(exclude_user)

        sent = 0
        for uid in agent_ids:
            sent += await self.send_to_user(uid, event)
        return sent

    async def subscribe(self, ws: WebSocket, channel: str) -> None:
        """订阅频道。"""
        user_id = self._ws_to_user.get(ws)
        if not user_id:
            return
        async with self._lock:
            self._subscriptions[user_id].add(channel)

    async def unsubscribe(self, ws: WebSocket, channel: str) -> None:
        """取消订阅频道。"""
        user_id = self._ws_to_user.get(ws)
        if not user_id:
            return
        async with self._lock:
            self._subscriptions[user_id].discard(channel)

    async def update_heartbeat(self, ws: WebSocket) -> None:
        """更新心跳时间。"""
        user_id = self._ws_to_user.get(ws)
        if user_id:
            self._heartbeats[user_id] = time.time()

    def is_connected(self, user_id: str) -> bool:
        """检查用户是否在线。"""
        return user_id in self._connections and bool(self._connections[user_id])

    def get_online_agents(self, project_id: str) -> List[str]:
        """获取项目下在线客服 ID 列表。"""
        return list(self._project_agents.get(project_id, set()))

    async def _send_json(self, ws: WebSocket, data: dict) -> None:
        """发送 JSON 消息（带异常保护）。"""
        try:
            await ws.send_json(data)
        except WebSocketDisconnect:
            await self.disconnect(ws)
        except Exception as e:
            logger.warning(f"WebSocket send_json 异常: {e}")
            await self.disconnect(ws)

    def _count_connections(self) -> int:
        return len(self._ws_to_user)

    async def _heartbeat_loop(self) -> None:
        """心跳检查循环：定期清理超时连接。"""
        while self._running:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            now = time.time()
            stale_users = []
            async with self._lock:
                for uid, last_hb in list(self._heartbeats.items()):
                    if now - last_hb > HEARTBEAT_TIMEOUT:
                        stale_users.append(uid)

            for uid in stale_users:
                logger.warning(f"心跳超时，断开 user={uid[:12]}")
                async with self._lock:
                    connections = set(self._connections.get(uid, set()))
                for ws in connections:
                    try:
                        await ws.close(code=1001, reason="心跳超时")
                    except Exception:
                        pass
                    await self.disconnect(ws)


# 全局单例
_manager: Optional[ConnectionManager] = None


def get_manager() -> ConnectionManager:
    """获取全局 ConnectionManager 单例。"""
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager