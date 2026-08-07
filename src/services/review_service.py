"""审核与版本回滚服务。

文档状态流程：
  draft (草稿) → pending (待审核) → approved (已通过) → 检索可见
                                          → rejected (已拒绝) → draft
"""

import json
import os
import sqlite3
import threading
import time
from typing import List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS document_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '[]',
    status TEXT DEFAULT 'draft',
    version INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dv_doc ON document_versions(doc_id, project_id);
"""


class ReviewService:
    """审核与版本回滚服务。"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or "data/review.db"
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

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def save_version(
        self,
        doc_id: str,
        project_id: str,
        title: str,
        content: str,
        tags: Optional[List[str]] = None,
        status: str = "draft",
    ) -> int:
        """保存文档版本。返回版本号。"""
        now = int(time.time())
        with self._lock:
            conn = self._get_connection()
            try:
                # 获取当前最大版本号
                row = conn.execute(
                    "SELECT MAX(version) as mv FROM document_versions WHERE doc_id = ? AND project_id = ?",
                    (doc_id, project_id),
                ).fetchone()
                version = (row["mv"] or 0) + 1

                conn.execute(
                    """INSERT INTO document_versions
                    (doc_id, project_id, title, content, tags, status, version, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (doc_id, project_id, title, content,
                     json.dumps(tags or [], ensure_ascii=False),
                     status, version, now),
                )
                conn.commit()
                return version
            finally:
                conn.close()

    def get_versions(self, doc_id: str, project_id: str) -> List[dict]:
        """获取文档版本历史。"""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """SELECT * FROM document_versions
                WHERE doc_id = ? AND project_id = ?
                ORDER BY version DESC""",
                (doc_id, project_id),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "version": r["version"],
                    "title": r["title"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_version(self, doc_id: str, project_id: str, version: int) -> Optional[dict]:
        """获取指定版本的内容。"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT * FROM document_versions
                WHERE doc_id = ? AND project_id = ? AND version = ?""",
                (doc_id, project_id, version),
            ).fetchone()
            if not row:
                return None
            return {
                "title": row["title"],
                "content": row["content"],
                "tags": json.loads(row["tags"]),
                "version": row["version"],
            }
        finally:
            conn.close()

    def submit_review(self, doc_id: str, project_id: str) -> None:
        """提交审核：draft → pending。"""
        now = int(time.time())
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """UPDATE document_versions SET status = 'pending', created_at = ?
                    WHERE doc_id = ? AND project_id = ? AND status = 'draft'""",
                    (now, doc_id, project_id),
                )
                conn.commit()
            finally:
                conn.close()

    def approve(self, doc_id: str, project_id: str) -> None:
        """审核通过：pending → approved。"""
        now = int(time.time())
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """UPDATE document_versions SET status = 'approved', created_at = ?
                    WHERE doc_id = ? AND project_id = ? AND status = 'pending'""",
                    (now, doc_id, project_id),
                )
                conn.commit()
            finally:
                conn.close()

    def reject(self, doc_id: str, project_id: str) -> None:
        """审核拒绝：pending → draft。"""
        now = int(time.time())
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """UPDATE document_versions SET status = 'draft', created_at = ?
                    WHERE doc_id = ? AND project_id = ? AND status = 'pending'""",
                    (now, doc_id, project_id),
                )
                conn.commit()
            finally:
                conn.close()

    def get_status(self, doc_id: str, project_id: str) -> str:
        """获取文档当前状态。"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT status FROM document_versions
                WHERE doc_id = ? AND project_id = ?
                ORDER BY version DESC LIMIT 1""",
                (doc_id, project_id),
            ).fetchone()
            return row["status"] if row else "draft"
        finally:
            conn.close()