"""话术库服务：预置快捷回复的 CRUD。

话术分两级：
- 项目级（is_global=true）：管理员预设，所有客服可见
- 个人级（is_global=false）：客服自己保存
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS canned_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT DEFAULT '',
    shortcut TEXT DEFAULT '',
    is_global INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_canned_project ON canned_responses(project_id);
CREATE INDEX IF NOT EXISTS idx_canned_user ON canned_responses(user_id);
CREATE INDEX IF NOT EXISTS idx_canned_cat ON canned_responses(category);
"""


class CannedResponseService:
    """话术库服务。"""

    DEFAULT_CATEGORIES = ["问候", "常见问题", "退换货", "物流", "投诉", "结束语", "其他"]

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or "data/canned_responses.db"
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
                conn.commit()
            finally:
                conn.close()
            logger.info(f"话术库数据库已初始化: {self._db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def create(
        self,
        project_id: str,
        user_id: str,
        title: str,
        content: str,
        category: str = "",
        shortcut: str = "",
        is_global: bool = False,
    ) -> int:
        """创建话术。返回 ID。"""
        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    """INSERT INTO canned_responses
                    (project_id, user_id, title, content, category, shortcut, is_global, sort_order, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (project_id, user_id, title, content, category, shortcut, int(is_global), 0, now, now),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def update(self, response_id: int, **kwargs) -> bool:
        """更新话术。"""
        now = int(datetime.now(timezone.utc).timestamp())
        allowed = {"title", "content", "category", "shortcut", "is_global", "sort_order"}
        updates = []
        params = []
        for k, v in kwargs.items():
            if k in allowed:
                if k == "is_global":
                    v = int(v)
                updates.append(f"{k} = ?")
                params.append(v)
        if not updates:
            return False
        updates.append("updated_at = ?")
        params.append(now)
        params.append(response_id)

        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    f"UPDATE canned_responses SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def delete(self, response_id: int) -> bool:
        """删除话术。"""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute("DELETE FROM canned_responses WHERE id = ?", (response_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def list(
        self,
        project_id: str,
        user_id: str = "",
        category: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """列出话术（项目级 + 个人级）。"""
        conditions = ["project_id = ?"]
        params = [project_id]

        if user_id:
            conditions.append("(is_global = 1 OR user_id = ?)")
            params.append(user_id)
        if category:
            conditions.append("category = ?")
            params.append(category)

        where = " AND ".join(conditions)
        offset = (page - 1) * page_size

        conn = self._get_connection()
        try:
            count = conn.execute(
                f"SELECT COUNT(*) as cnt FROM canned_responses WHERE {where}", params
            ).fetchone()["cnt"]

            rows = conn.execute(
                f"""SELECT * FROM canned_responses WHERE {where}
                ORDER BY is_global DESC, sort_order ASC, created_at DESC
                LIMIT ? OFFSET ?""",
                params + [page_size, offset],
            ).fetchall()

            items = [self._row_to_dict(r) for r in rows]
            return {"items": items, "total": count, "page": page, "page_size": page_size}
        finally:
            conn.close()

    def get_by_id(self, response_id: int) -> Optional[dict]:
        """获取单条话术。"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM canned_responses WHERE id = ?", (response_id,)
            ).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def list_categories(self, project_id: str) -> List[str]:
        """获取项目下的话术分类列表。"""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT category FROM canned_responses WHERE project_id = ? AND category != '' ORDER BY category",
                (project_id,),
            ).fetchall()
            return [r["category"] for r in rows]
        finally:
            conn.close()

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "content": row["content"],
            "category": row["category"],
            "shortcut": row["shortcut"],
            "is_global": bool(row["is_global"]),
            "sort_order": row["sort_order"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }