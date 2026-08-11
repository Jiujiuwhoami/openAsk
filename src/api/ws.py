"""WebSocket 实时通信路由。

端点：
  - ws://host/ws?token=<jwt>          客服连接（JWT 鉴权）
  - ws://host/ws?api_key=<key>&project_id=<pid>   Widget 用户连接（API Key 鉴权）

事件协议（客户端 → 服务端）：
  {"type": "subscribe", "data": {"channel": "conversation:conv_123"}}
  {"type": "unsubscribe", "data": {"channel": "conversation:conv_123"}}
  {"type": "message.send", "data": {"conversation_id": "conv_123", "content": "..."}}
  {"type": "message.typing", "data": {"conversation_id": "conv_123"}}
  {"type": "heartbeat"}

事件协议（服务端 → 客户端）：
  {"type": "connected", "data": {"user_id": "...", "is_agent": true}}
  {"type": "message.new", "data": {"conversation_id": "...", "message": {...}}}
  {"type": "message.typing", "data": {"conversation_id": "...", "user_id": "..."}}
  {"type": "conversation.status", "data": {"conversation_id": "...", "status": "...", "agent_id": "..."}}
  {"type": "handoff.new", "data": {"handoff": {...}}}
  {"type": "error", "data": {"message": "..."}}
"""

import json
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.services.user_service import UserService
from src.services.project_service import ProjectService
from src.services.conversation_service import ConversationService
from src.services.ws_manager import get_manager
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()

_conv_service = ConversationService()
_project_service = ProjectService()
_user_service = UserService()

# 频道前缀
CHANNEL_CONVERSATION = "conversation:"
CHANNEL_PROJECT = "project:"


async def _authenticate_agent(token: str) -> Optional[dict]:
    """通过 JWT 验证客服身份。返回 {user_id, is_agent} 或 None。"""
    payload = UserService.decode_access_token(token)
    if payload is None:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    user = _user_service.get_by_id(user_id)
    if user is None or not user.is_active:
        return None
    return {"user_id": user_id, "is_agent": True, "user": user}


async def _authenticate_widget(api_key: str, project_id: str) -> Optional[dict]:
    """通过 API Key 验证 Widget 用户身份。返回 {user_id, is_agent} 或 None。"""
    project = _project_service.get_by_api_key(api_key)
    if project is None or not project.is_active:
        return None
    if project.project_id != project_id:
        return None
    # Widget 用户使用项目 ID 作为 user_id 前缀
    return {"user_id": f"widget_{project_id}", "is_agent": False, "project": project}


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    token: Optional[str] = Query(None),
    api_key: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
):
    """WebSocket 主端点：处理实时消息。"""
    manager = get_manager()

    # 鉴权
    if token:
        auth = await _authenticate_agent(token)
        if auth is None:
            await ws.close(code=1008, reason="无效的认证 token")
            return
        user_id = auth["user_id"]
        is_agent = True
        # 客服可能属于多个项目，注册项目频道
        projects = _project_service.list_by_user(user_id)
        project_ids = [p.project_id for p in projects] if projects else []
    elif api_key and project_id:
        auth = await _authenticate_widget(api_key, project_id)
        if auth is None:
            await ws.close(code=1008, reason="无效的 API Key 或项目")
            return
        user_id = auth["user_id"]
        is_agent = False
        project_ids = [project_id]
    else:
        await ws.close(code=1008, reason="缺少认证信息")
        return

    # 建立连接（客服注册到项目频道）
    await manager.connect(
        ws,
        user_id,
        project_id=project_ids[0] if project_ids else "",
        is_agent=is_agent,
    )

    # 通知客服所在项目的所有在线客服（新客服上线）
    if is_agent:
        for pid in project_ids:
            await manager.broadcast_to_project(
                pid,
                {"type": "agent.status", "data": {"user_id": user_id, "status": "online"}},
                exclude_user=user_id,
            )

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_to_user(user_id, {
                    "type": "error",
                    "data": {"message": "无效的 JSON 消息"},
                })
                # 继续循环
                continue

            event_type = msg.get("type", "")
            data = msg.get("data", {}) or {}
            await _handle_event(manager, ws, user_id, is_agent, project_ids, event_type, data)

    except WebSocketDisconnect:
        logger.info(f"WebSocket 断开: user={user_id[:12]}")
    except Exception as e:
        logger.warning(f"WebSocket 异常: user={user_id[:12]}, err={e}")
    finally:
        await manager.disconnect(ws)
        # 通知客服下线
        if is_agent:
            for pid in project_ids:
                await manager.broadcast_to_project(
                    pid,
                    {"type": "agent.status", "data": {"user_id": user_id, "status": "offline"}},
                    exclude_user=user_id,
                )


async def _handle_event(
    manager,
    ws,
    user_id: str,
    is_agent: bool,
    project_ids: list,
    event_type: str,
    data: dict,
) -> None:
    """处理客户端事件。"""
    if event_type == "heartbeat":
        await manager.update_heartbeat(ws)
        return

    if event_type == "subscribe":
        channel = data.get("channel", "")
        if channel:
            await manager.subscribe(ws, channel)
        return

    if event_type == "unsubscribe":
        channel = data.get("channel", "")
        if channel:
            await manager.unsubscribe(ws, channel)
        return

    if event_type == "message.send":
        await _handle_message_send(manager, ws, user_id, is_agent, data)
        return

    if event_type == "message.typing":
        conversation_id = data.get("conversation_id", "")
        if conversation_id:
            await manager.broadcast_to_project(
                _get_project_for_conversation(conversation_id) or "",
                {"type": "message.typing", "data": {"conversation_id": conversation_id, "user_id": user_id}},
                exclude_user=user_id,
            )
        return

    if event_type == "subscribe.project":
        # 客服订阅整个项目的待接单事件
        for pid in project_ids:
            await manager.subscribe(ws, f"{CHANNEL_PROJECT}{pid}")
        return


def _get_project_for_conversation(conversation_id: str) -> Optional[str]:
    """获取会话所属项目 ID。"""
    conv = _conv_service.get_conversation(conversation_id)
    return conv.project_id if conv else None


async def _handle_message_send(
    manager,
    ws,
    user_id: str,
    is_agent: bool,
    data: dict,
) -> None:
    """处理消息发送事件。

    - 客服发送：role='agent'，保存到数据库，推送给订阅该会话的用户
    - Widget 用户发送：role='user'，保存到数据库，推送给该项目的客服
    """
    conversation_id = data.get("conversation_id", "")
    content = data.get("content", "")
    if not conversation_id or not content:
        await manager.send_to_user(user_id, {
            "type": "error",
            "data": {"message": "缺少 conversation_id 或 content"},
        })
        return

    conv = _conv_service.get_conversation(conversation_id)
    if conv is None:
        await manager.send_to_user(user_id, {
            "type": "error",
            "data": {"message": "会话不存在"},
        })
        return

    try:
        if is_agent:
            # 客服发送消息
            if conv.status != "agent":
                await manager.send_to_user(user_id, {
                    "type": "error",
                    "data": {"message": "会话未被接管，无法发送客服消息"},
                })
                return
            msg = _conv_service.add_message(
                conversation_id,
                "agent",
                content,
                metadata=json.dumps({"agent_id": user_id}),
            )
        else:
            # Widget 用户发送消息（人工模式）
            if conv.status != "agent":
                await manager.send_to_user(user_id, {
                    "type": "error",
                    "data": {"message": "会话未被人工接管，请使用 AI 对话"},
                })
                return
            msg = _conv_service.add_message(conversation_id, "user", content)

        message_data = {
            "id": msg.id,
            "conversation_id": conversation_id,
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at,
        }

        if is_agent:
            # 推送给订阅了该会话的用户（Widget 用户）
            channel = f"{CHANNEL_CONVERSATION}{conversation_id}"
            await _notify_channel_subscribers(manager, channel, {
                "type": "message.new",
                "data": {"conversation_id": conversation_id, "message": message_data},
            })
        else:
            # 推送给该项目的客服
            await manager.broadcast_to_project(conv.project_id, {
                "type": "message.new",
                "data": {"conversation_id": conversation_id, "message": message_data},
            })

        # 回执给发送者
        await manager.send_to_user(user_id, {
            "type": "message.sent",
            "data": {"message": message_data},
        })

    except Exception as e:
        logger.error(f"WebSocket 消息发送失败: {e}")
        await manager.send_to_user(user_id, {
            "type": "error",
            "data": {"message": f"消息发送失败: {str(e)}"},
        })


async def _notify_channel_subscribers(manager, channel: str, event: dict) -> None:
    """通知订阅了指定频道的所有用户。"""
    # 简化实现：遍历所有连接，检查订阅
    # 生产环境应使用 Redis Pub/Sub 支持多实例
    for uid, channels in list(manager._subscriptions.items()):
        if channel in channels:
            await manager.send_to_user(uid, event)


# ================================================================
# 事件推送辅助函数（供业务路由调用）
# ================================================================

async def notify_conversation_status(
    conversation_id: str,
    status: str,
    agent_id: str = "",
) -> None:
    """推送会话状态变更给订阅者。"""
    manager = get_manager()
    channel = f"{CHANNEL_CONVERSATION}{conversation_id}"
    await _notify_channel_subscribers(manager, channel, {
        "type": "conversation.status",
        "data": {
            "conversation_id": conversation_id,
            "status": status,
            "agent_id": agent_id,
        },
    })


async def notify_handoff_new(project_id: str, handoff: dict) -> None:
    """推送新转接请求给项目客服。"""
    manager = get_manager()
    await manager.broadcast_to_project(project_id, {
        "type": "handoff.new",
        "data": {"handoff": handoff},
    })