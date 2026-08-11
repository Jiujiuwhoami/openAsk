"""客服服务：在线状态管理、技能组、负载跟踪。

管理客服的在线/忙碌/离开/离线状态，
支持自动心跳检测和服务端状态同步。
"""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from src.utils.logger import get_logger

logger = get_logger(__name__)

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS agent_status (
    user_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'offline'
        CHECK(status IN ('online', 'busy', 'away', 'offline')),
    current_load INTEGER NOT NULL DEFAULT 0,
    max_load INTEGER NOT NULL DEFAULT 5,
    skills TEXT DEFAULT '[]',
    auto_accept BOOLEAN NOT NULL DEFAULT 1,
    last_heartbeat INTEGER NOT NULL DEFAULT 0,
    total_assigned INTEGER NOT NULL DEFAULT 0,
    last_assigned_at INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_status_project ON agent_status(project_id);
CREATE INDEX IF NOT EXISTS idx_agent_status_status ON agent_status(status);
"""


class AgentService:
    """客服服务：状态管理、负载跟踪。"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or "data/agents.db"
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
            logger.info(f"客服状态数据库已初始化: {self._db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """旧库迁移（幂等）。"""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(agent_status)").fetchall()}
        if "total_assigned" not in cols:
            conn.execute("ALTER TABLE agent_status ADD COLUMN total_assigned INTEGER DEFAULT 0")
            logger.info("agent_status 表迁移：新增 total_assigned 列")
        if "last_assigned_at" not in cols:
            conn.execute("ALTER TABLE agent_status ADD COLUMN last_assigned_at INTEGER DEFAULT 0")
            logger.info("agent_status 表迁移：新增 last_assigned_at 列")

    def set_status(
        self,
        user_id: str,
        project_id: str,
        status: str,
        max_load: int = 5,
        auto_accept: bool = True,
        skills: Optional[List[str]] = None,
    ) -> bool:
        """设置客服在线状态。"""
        now = int(datetime.now(timezone.utc).timestamp())
        skills_json = json.dumps(skills or [], ensure_ascii=False)
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    """INSERT INTO agent_status
                    (user_id, project_id, status, current_load, max_load, skills, auto_accept, last_heartbeat, created_at, updated_at)
                    VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        status = ?,
                        max_load = ?,
                        skills = ?,
                        auto_accept = ?,
                        updated_at = ?""",
                    (
                        user_id, project_id, status, max_load, skills_json, auto_accept, now, now, now,
                        status, max_load, skills_json, auto_accept, now,
                    ),
                )
                conn.commit()
                updated = cur.rowcount > 0
                logger.info(f"客服状态更新: user={user_id[:12]}, project={project_id[:12]}, status={status}")
                return updated
            finally:
                conn.close()

    def get_status(self, user_id: str) -> Optional[dict]:
        """获取指定客服的状态。"""
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute(
                    "SELECT * FROM agent_status WHERE user_id = ?", (user_id,)
                ).fetchone()
                if not row:
                    return None
                return self._row_to_dict(row)
            finally:
                conn.close()

    def list_project_agents(self, project_id: str) -> List[dict]:
        """列出项目的所有客服状态。"""
        with self._lock:
            conn = self._get_connection()
            try:
                rows = conn.execute(
                    "SELECT * FROM agent_status WHERE project_id = ? ORDER BY status, updated_at DESC",
                    (project_id,),
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def list_online_agents(self, project_id: str) -> List[dict]:
        """列出项目下在线客服（online + busy）。"""
        with self._lock:
            conn = self._get_connection()
            try:
                rows = conn.execute(
                    "SELECT * FROM agent_status WHERE project_id = ? AND status IN ('online', 'busy') ORDER BY updated_at DESC",
                    (project_id,),
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def update_heartbeat(self, user_id: str) -> bool:
        """更新心跳时间。"""
        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    "UPDATE agent_status SET last_heartbeat = ?, updated_at = ? WHERE user_id = ?",
                    (now, now, user_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def increment_load(self, user_id: str) -> bool:
        """增加客服负载计数（接单时调用）。同时累加分配总数。"""
        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    """UPDATE agent_status SET
                        current_load = current_load + 1,
                        total_assigned = total_assigned + 1,
                        last_assigned_at = ?,
                        updated_at = ?
                    WHERE user_id = ? AND current_load < max_load""",
                    (now, now, user_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def decrement_load(self, user_id: str) -> bool:
        """减少客服负载计数（释放时调用）。"""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    """UPDATE agent_status SET
                        current_load = MAX(0, current_load - 1),
                        updated_at = ?
                    WHERE user_id = ?""",
                    (int(datetime.now(timezone.utc).timestamp()), user_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def get_available_agent(
        self,
        project_id: str,
        strategy: str = "least_busy",
        required_skills: Optional[List[str]] = None,
    ) -> Optional[dict]:
        """获取项目下可用的在线客服。

        分配策略（strategy）：
        - least_busy: 最少繁忙（按 current_load 升序，online 优先）
        - round_robin: 轮询分配（按 total_assigned 升序，分配次数最少优先）
        - skill_match: 技能匹配（优先匹配 required_skills 的客服，再按负载排序）

        Args:
            project_id: 项目 ID
            strategy: 分配策略
            required_skills: 技能匹配策略下需要的技能列表

        Returns:
            agent 状态 dict 或 None
        """
        with self._lock:
            conn = self._get_connection()
            try:
                if strategy == "round_robin":
                    order = "total_assigned ASC, CASE status WHEN 'online' THEN 0 ELSE 1 END, current_load ASC"
                elif strategy == "skill_match" and required_skills:
                    # 优先匹配技能，再按负载
                    order = (
                        "CASE WHEN skills LIKE '%" + required_skills[0] + "%' THEN 0 ELSE 1 END, "
                        "CASE status WHEN 'online' THEN 0 ELSE 1 END, "
                        "current_load ASC"
                    )
                else:
                    # least_busy (默认)
                    order = "CASE status WHEN 'online' THEN 0 ELSE 1 END, current_load ASC"

                row = conn.execute(
                    f"""SELECT * FROM agent_status
                    WHERE project_id = ? AND status IN ('online', 'busy') AND current_load < max_load
                    ORDER BY {order}
                    LIMIT 1""",
                    (project_id,),
                ).fetchone()
                if not row:
                    return None
                return self._row_to_dict(row)
            finally:
                conn.close()

    def cleanup_stale_agents(self, timeout_seconds: int = 120) -> int:
        """清理心跳超时的客服，将其标记为离线。返回清理数量。"""
        cutoff = int(datetime.now(timezone.utc).timestamp()) - timeout_seconds
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    "UPDATE agent_status SET status = 'offline', updated_at = ? WHERE status != 'offline' AND last_heartbeat < ?",
                    (int(datetime.now(timezone.utc).timestamp()), cutoff),
                )
                conn.commit()
                cleaned = cur.rowcount
                if cleaned > 0:
                    logger.info(f"心跳超时清理: {cleaned} 个客服已标记离线")
                return cleaned
            finally:
                conn.close()

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        return {
            "user_id": row["user_id"],
            "project_id": row["project_id"],
            "status": row["status"],
            "current_load": row["current_load"],
            "max_load": row["max_load"],
            "skills": row["skills"],
            "auto_accept": bool(row["auto_accept"]),
            "last_heartbeat": row["last_heartbeat"],
            "total_assigned": row["total_assigned"] if "total_assigned" in row.keys() else 0,
            "last_assigned_at": row["last_assigned_at"] if "last_assigned_at" in row.keys() else 0,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }