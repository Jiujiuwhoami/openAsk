"""会话服务：多轮对话管理，支持会话创建、消息追加、历史查询。

每个会话（Conversation）包含一组按时间线排列的消息（Message）。
会话属于一个项目（project_id），前端只传 conversation_id 即可恢复上下文。
"""

import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from typing import List, Optional

from src.domain.conversation import Conversation, Message
from src.utils.logger import get_logger

logger = get_logger(__name__)

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    agent_id TEXT DEFAULT '',
    message_count INTEGER NOT NULL DEFAULT 0,
    tags TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_project ON conversations(project_id);
CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system', 'agent')),
    content TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'text',
    metadata TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_msg_created ON messages(created_at);
"""


class ConversationService:
    """会话服务：CRUD + 消息管理。"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or "data/conversations.db"
        self._lock = threading.RLock()
        self._ensure_db()

    def _ensure_db(self) -> None:
        dir_path = os.path.dirname(self._db_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with self._lock:
            conn = self._get_connection()
            try:
                conn.executescript(_INIT_SQL)
                self._migrate(conn)
                conn.commit()
            finally:
                conn.close()
            logger.info(f"会话数据库已初始化: {self._db_path}")

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """将旧版 schema 迁移到新版。

        1. conversations 表补 agent_id 列
        2. conversations 表补 tags 列
        3. messages 表 role CHECK 约束加入 'agent'（SQLite 需重建表）
        4. messages 表补 message_type 列
        """
        # 1. conversations.agent_id
        cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(conversations)").fetchall()
        }
        if "agent_id" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN agent_id TEXT DEFAULT ''")
            logger.info("迁移: conversations 表新增 agent_id 列")

        # 2. conversations.tags
        if "tags" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
            logger.info("迁移: conversations 表新增 tags 列")

        # 2. messages 表 CHECK 约束
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone()
        if row and "'agent'" not in row["sql"]:
            conn.executescript(
                """
                ALTER TABLE messages RENAME TO messages_old;

                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system', 'agent')),
                    content TEXT NOT NULL,
                    message_type TEXT NOT NULL DEFAULT 'text',
                    metadata TEXT DEFAULT '',
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                );

                INSERT INTO messages (id, conversation_id, role, content, metadata, created_at)
                SELECT id, conversation_id, role, content, metadata, created_at FROM messages_old;

                DROP TABLE messages_old;

                CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
                CREATE INDEX IF NOT EXISTS idx_msg_created ON messages(created_at);
                """
            )
            logger.info("迁移: messages 表 role 约束加入 'agent'")

        # 3. messages 表补 message_type 列（兼容未重建的场景）
        msg_cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "message_type" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN message_type TEXT NOT NULL DEFAULT 'text'")
            logger.info("迁移: messages 表新增 message_type 列")

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ---- 会话 CRUD ----

    def create_conversation(self, project_id: str, title: str = "") -> Conversation:
        """创建新会话。返回 Conversation 实例。"""
        conv_id = "conv_" + secrets.token_hex(8)
        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """INSERT INTO conversations (id, project_id, title, status, message_count, created_at, updated_at)
                    VALUES (?, ?, ?, 'active', 0, ?, ?)""",
                    (conv_id, project_id, title[:200], now, now),
                )
                conn.commit()
            finally:
                conn.close()
        logger.info(f"会话创建成功: {conv_id} (project={project_id})")
        return self.get_conversation(conv_id)

    def update_status(self, conversation_id: str, status: str, agent_id: str = "") -> bool:
        """更新会话状态及 agent_id。"""
        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    "UPDATE conversations SET status = ?, agent_id = ?, updated_at = ? WHERE id = ?",
                    (status, agent_id, now, conversation_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def get_messages_since(self, conversation_id: str, since_id: int = 0) -> List[Message]:
        """获取会话中 id > since_id 的所有消息，按 id 正序。"""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? AND id > ? ORDER BY id ASC",
                (conversation_id, since_id),
            ).fetchall()
            return [
                Message(
                    id=r["id"],
                    conversation_id=r["conversation_id"],
                    role=r["role"],
                    content=r["content"],
                    message_type=r["message_type"] if "message_type" in r.keys() else "text",
                    metadata=r["metadata"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """根据 ID 获取会话。"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if not row:
                return None
            tags = json.loads(row["tags"]) if "tags" in row.keys() and row["tags"] else []
            return Conversation(
                conversation_id=row["id"],
                project_id=row["project_id"],
                title=row["title"],
                status=row["status"],
                message_count=row["message_count"],
                agent_id=row["agent_id"] if "agent_id" in row.keys() else "",
                tags=tags,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        finally:
            conn.close()

    def update_title(self, conversation_id: str, title: str) -> bool:
        """更新会话标题。"""
        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                    (title[:200], now, conversation_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def _get_tags(self, conn: sqlite3.Connection, conversation_id: str) -> list:
        """读取会话标签。"""
        row = conn.execute(
            "SELECT tags FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if not row:
            return []
        try:
            import json
            return json.loads(row["tags"]) if row["tags"] else []
        except (json.JSONDecodeError, TypeError):
            return []

    def add_tag(self, conversation_id: str, tag: str) -> list:
        """添加会话标签。返回更新后的标签列表。"""
        with self._lock:
            conn = self._get_connection()
            try:
                tags = self._get_tags(conn, conversation_id)
                if tag not in tags:
                    tags.append(tag)
                    conn.execute(
                        "UPDATE conversations SET tags = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(tags, ensure_ascii=False),
                         int(datetime.now(timezone.utc).timestamp()), conversation_id),
                    )
                    conn.commit()
                return tags
            finally:
                conn.close()

    def remove_tag(self, conversation_id: str, tag: str) -> list:
        """移除会话标签。返回更新后的标签列表。"""
        with self._lock:
            conn = self._get_connection()
            try:
                tags = self._get_tags(conn, conversation_id)
                if tag in tags:
                    tags.remove(tag)
                    conn.execute(
                        "UPDATE conversations SET tags = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(tags, ensure_ascii=False),
                         int(datetime.now(timezone.utc).timestamp()), conversation_id),
                    )
                    conn.commit()
                return tags
            finally:
                conn.close()

    def get_tags(self, conversation_id: str) -> list:
        """获取会话标签。"""
        conn = self._get_connection()
        try:
            return self._get_tags(conn, conversation_id)
        finally:
            conn.close()

    def list_project_tags(self, project_id: str) -> dict:
        """获取项目所有标签及其使用次数。"""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT tags FROM conversations WHERE project_id = ? AND tags != '[]'",
                (project_id,),
            ).fetchall()
            tag_count = {}
            for r in rows:
                try:
                    tags = json.loads(r["tags"]) if r["tags"] else []
                except (json.JSONDecodeError, TypeError):
                    tags = []
                for t in tags:
                    tag_count[t] = tag_count.get(t, 0) + 1
            items = sorted(tag_count.items(), key=lambda x: -x[1])
            return {"items": [{"tag": t, "count": c} for t, c in items]}
        finally:
            conn.close()

    def delete_conversation(self, conversation_id: str) -> bool:
        """删除会话及其所有消息（物理删除）。"""
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
                cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def list_conversations(
        self,
        project_id: str,
        page: int = 1,
        page_size: int = 20,
        status: str = "",
    ) -> dict:
        """分页列出项目的会话，可选按状态筛选。"""
        conditions = ["project_id = ?"]
        params = [project_id]
        if status:
            conditions.append("status = ?")
            params.append(status)

        where = " AND ".join(conditions)
        offset = (page - 1) * page_size
        conn = self._get_connection()
        try:
            count_row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM conversations WHERE {where}",
                params,
            ).fetchone()
            total = count_row["cnt"] if count_row else 0

            rows = conn.execute(
                f"""SELECT * FROM conversations WHERE {where}
                ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
                params + [page_size, offset],
            ).fetchall()

            items = []
            for r in rows:
                tag_list = json.loads(r["tags"]) if "tags" in r.keys() and r["tags"] else []
                items.append({
                    "conversation_id": r["id"],
                    "project_id": r["project_id"],
                    "title": r["title"],
                    "status": r["status"],
                    "agent_id": r["agent_id"] if "agent_id" in r.keys() else "",
                    "message_count": r["message_count"],
                    "tags": tag_list,
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                })

            return {"items": items, "total": total, "page": page, "page_size": page_size}
        finally:
            conn.close()

    # ---- 消息管理 ----

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        metadata: Optional[str] = None,
    ) -> Message:
        """向会话追加一条消息。自动更新会话的 message_count 和 updated_at。"""
        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    """INSERT INTO messages (conversation_id, role, content, message_type, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (conversation_id, role, content, message_type, metadata or "", now),
                )
                msg_id = cur.lastrowid

                conn.execute(
                    """UPDATE conversations SET
                        message_count = message_count + 1,
                        updated_at = ?
                    WHERE id = ?""",
                    (now, conversation_id),
                )
                conn.commit()
            finally:
                conn.close()

        return Message(
            id=msg_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            message_type=message_type,
            metadata=metadata,
            created_at=now,
        )

    def get_history(
        self,
        conversation_id: str,
        limit: int = 10,
    ) -> List[Message]:
        """获取会话最近 N 轮消息（按时间正序）。

        limit 限制的是轮数（一条 user + 一条 assistant = 1 轮）。
        默认返回最近 10 轮 = 20 条消息。
        """
        conn = self._get_connection()
        try:
            # 使用子查询取最新的 N 条消息 id，然后按 id 正序返回
            rows = conn.execute(
                """SELECT * FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?""",
                (conversation_id, limit * 2),
            ).fetchall()
            # 反转为正序
            rows.reverse()
            return [
                Message(
                    id=r["id"],
                    conversation_id=r["conversation_id"],
                    role=r["role"],
                    content=r["content"],
                    message_type=r["message_type"] if "message_type" in r.keys() else "text",
                    metadata=r["metadata"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    def get_messages_by_conversation(
        self,
        conversation_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """分页获取会话的所有消息。"""
        offset = (page - 1) * page_size
        conn = self._get_connection()
        try:
            count_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            total = count_row["cnt"] if count_row else 0

            rows = conn.execute(
                """SELECT * FROM messages WHERE conversation_id = ?
                ORDER BY created_at ASC LIMIT ? OFFSET ?""",
                (conversation_id, page_size, offset),
            ).fetchall()

            items = [
                {
                    "id": r["id"],
                    "conversation_id": r["conversation_id"],
                    "role": r["role"],
                    "content": r["content"],
                    "message_type": r["message_type"] if "message_type" in r.keys() else "text",
                    "metadata": r["metadata"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
            return {"items": items, "total": total, "page": page, "page_size": page_size}
        finally:
            conn.close()

    def get_history_as_messages(
        self,
        conversation_id: str,
        limit: int = 10,
    ) -> List[dict]:
        """获取会话历史，返回 OpenAI 格式的 messages 列表（用于 LLM 上下文）。
        过滤掉人工客服消息（role='agent'），避免 LLM 接收到非 AI 的对话内容。"""
        history = self.get_history(conversation_id, limit=limit)
        return [msg.to_dict() for msg in history if msg.role != "agent"]