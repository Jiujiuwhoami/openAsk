"""项目服务：CRUD、API Key 鉴权、统计。

每个项目（Project）是一个独立的知识库空间，拥有独立的 API Key 和 LLM 配置。
"""

import os
import secrets
import sqlite3
import threading
from datetime import datetime
from typing import List, Optional

from src.domain.project import Project
from src.domain.exceptions import ProjectNotFoundError, ProjectSuspendedError
from src.utils.logger import get_logger

logger = get_logger(__name__)

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    api_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    llm_api_key TEXT DEFAULT '',
    llm_api_base TEXT DEFAULT '',
    llm_model TEXT DEFAULT '',
    llm_timeout INTEGER DEFAULT 30,
    rate_limit_per_user TEXT DEFAULT '60/minute',
    rate_limit_global TEXT DEFAULT '1000/minute',
    system_prompt TEXT DEFAULT '',
    language TEXT DEFAULT 'zh',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_api_key ON projects(api_key);
CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);

CREATE TABLE IF NOT EXISTS project_stats (
    project_id TEXT PRIMARY KEY,
    total_calls INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cache_hits INTEGER DEFAULT 0,
    last_call_at INTEGER DEFAULT 0
);
"""


def _project_from_row(row: dict) -> Project:
    """从 SQLite 行记录构建 Project 实例。"""
    r = dict(row)  # sqlite3.Row → dict（支持 .get()）
    return Project(
        project_id=r["project_id"],
        user_id=r["user_id"],
        api_key=r["api_key"],
        name=r["name"],
        status=r["status"],
        llm_api_key=r.get("llm_api_key", ""),
        llm_api_base=r.get("llm_api_base", ""),
        llm_model=r.get("llm_model", ""),
        llm_timeout=r.get("llm_timeout", 30),
        rate_limit_per_user=r.get("rate_limit_per_user", "60/minute"),
        rate_limit_global=r.get("rate_limit_global", "1000/minute"),
        system_prompt=r.get("system_prompt", ""),
        language=r.get("language", "zh"),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def _generate_api_key() -> str:
    """生成安全的 API Key。"""
    return "sk_" + secrets.token_hex(24)


class ProjectService:
    """项目管理服务：CRUD + API Key 鉴权 + 统计。"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or "data/projects.db"
        self._lock = threading.RLock()
        self._ensure_db()

    def _ensure_db(self) -> None:
        """确保数据库目录和表存在。"""
        dir_path = os.path.dirname(self._db_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with self._lock:
            conn = self._get_connection()
            try:
                conn.executescript(_INIT_SQL)
                # 兼容旧库：为已存在的 projects 表补充新列
                self._migrate(conn)
                conn.commit()
            finally:
                conn.close()
            logger.info(f"项目数据库已初始化: {self._db_path}")

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """旧库迁移：补加新增列（幂等，列不存在时才添加）。"""
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
        if "language" not in columns:
            conn.execute("ALTER TABLE projects ADD COLUMN language TEXT DEFAULT 'zh'")
            logger.info("项目表迁移：新增 language 列")

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接。"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ---- CRUD ----

    def create_project(
        self,
        user_id: str,
        name: str,
        api_key: Optional[str] = None,
        llm_api_key: str = "",
        llm_api_base: str = "",
        llm_model: str = "",
        llm_timeout: int = 30,
        rate_limit_per_user: str = "60/minute",
        rate_limit_global: str = "1000/minute",
        system_prompt: str = "",
        language: str = "zh",
    ) -> Project:
        """创建新项目，自动生成 API Key。

        Args:
            user_id: 所属用户 ID
            name: 项目名称
            api_key: 指定 API Key，不指定则自动生成
            language: 回答语言（zh/en）

        Returns:
            创建的 Project 实例
        """
        project_id = f"proj_{secrets.token_hex(8)}"
        key = api_key or _generate_api_key()
        now = int(datetime.utcnow().timestamp())

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """INSERT INTO projects (
                        project_id, user_id, api_key, name, status,
                        llm_api_key, llm_api_base, llm_model, llm_timeout,
                        rate_limit_per_user, rate_limit_global, system_prompt,
                        language, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id, user_id, key, name,
                        llm_api_key, llm_api_base, llm_model, llm_timeout,
                        rate_limit_per_user, rate_limit_global, system_prompt,
                        language, now, now,
                    ),
                )
                # 初始化 stats 记录
                conn.execute(
                    "INSERT INTO project_stats (project_id) VALUES (?)",
                    (project_id,),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                # 极低概率的 Key 冲突，重试一次
                return self.create_project(
                    user_id=user_id, name=name,
                    llm_api_key=llm_api_key, llm_api_base=llm_api_base,
                    llm_model=llm_model, llm_timeout=llm_timeout,
                    rate_limit_per_user=rate_limit_per_user,
                    rate_limit_global=rate_limit_global,
                    system_prompt=system_prompt,
                    language=language,
                )
            finally:
                conn.close()

        logger.info(f"项目创建成功: {project_id} ({name})")
        return self.get_by_id(project_id)

    def get_by_id(self, project_id: str) -> Optional[Project]:
        """根据项目 ID 获取项目。"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            return _project_from_row(row) if row else None
        finally:
            conn.close()

    def get_by_api_key(self, api_key: str) -> Optional[Project]:
        """根据 API Key 获取项目（用于请求鉴权）。

        只返回 active 状态的项目。
        """
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM projects WHERE api_key = ? AND status = 'active'",
                (api_key,),
            ).fetchone()
            return _project_from_row(row) if row else None
        finally:
            conn.close()

    def list_by_user(self, user_id: str) -> List[Project]:
        """获取用户的所有项目（排除已删除的）。"""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM projects WHERE user_id = ? AND status != 'deleted' ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            return [_project_from_row(r) for r in rows]
        finally:
            conn.close()

    def update_project(self, project_id: str, **kwargs) -> Project:
        """更新项目配置。

        Args:
            project_id: 项目 ID
            **kwargs: 要更新的字段（name, status, llm_api_key, 等）

        Returns:
            更新后的 Project 实例
        """
        project = self.get_by_id(project_id)
        if not project:
            raise ProjectNotFoundError(f"项目不存在: {project_id}")

        allowed_fields = {
            "name", "status", "llm_api_key", "llm_api_base", "llm_model",
            "llm_timeout", "rate_limit_per_user", "rate_limit_global", "system_prompt",
            "language",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields and v is not None}
        if not updates:
            return project

        now = int(datetime.utcnow().timestamp())
        set_clauses = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [now, project_id]

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    f"UPDATE projects SET {set_clauses}, updated_at = ? WHERE project_id = ?",
                    values,
                )
                conn.commit()
            finally:
                conn.close()

        logger.info(f"项目已更新: {project_id}")
        return self.get_by_id(project_id)

    def delete_project(self, project_id: str) -> bool:
        """删除项目（软删除：标记为 deleted）。"""
        project = self.get_by_id(project_id)
        if not project:
            return False

        now = int(datetime.utcnow().timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "UPDATE projects SET status = 'deleted', updated_at = ? WHERE project_id = ?",
                    (now, project_id),
                )
                conn.commit()
            finally:
                conn.close()

        logger.info(f"项目已删除: {project_id}")
        return True

    def rotate_api_key(self, project_id: str) -> str:
        """轮换项目 API Key。

        旧 key 立即失效，新 key 返回给调用方。
        """
        project = self.get_by_id(project_id)
        if not project:
            raise ProjectNotFoundError(f"项目不存在: {project_id}")

        new_key = _generate_api_key()
        now = int(datetime.utcnow().timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "UPDATE projects SET api_key = ?, updated_at = ? WHERE project_id = ?",
                    (new_key, now, project_id),
                )
                conn.commit()
            finally:
                conn.close()

        logger.info(f"API Key 已轮换: {project_id}")
        return new_key

    # ---- 统计 ----

    def get_stats(self, project_id: str) -> dict:
        """获取项目统计信息。"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM project_stats WHERE project_id = ?", (project_id,)
            ).fetchone()
            if not row:
                return {
                    "total_calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cache_hits": 0,
                    "cache_hit_rate": 0.0,
                    "last_call_at": 0,
                }
            total = row["total_calls"]
            hits = row["cache_hits"]
            return {
                "total_calls": total,
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "cache_hits": hits,
                "cache_hit_rate": round(hits / total, 4) if total > 0 else 0.0,
                "last_call_at": row["last_call_at"],
            }
        finally:
            conn.close()

    def record_call(
        self,
        project_id: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_hit: bool = False,
    ) -> None:
        """记录一次 API 调用。"""
        now = int(datetime.utcnow().timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                if cache_hit:
                    conn.execute(
                        """UPDATE project_stats SET
                            total_calls = total_calls + 1,
                            cache_hits = cache_hits + 1,
                            last_call_at = ?
                        WHERE project_id = ?""",
                        (now, project_id),
                    )
                else:
                    conn.execute(
                        """UPDATE project_stats SET
                            total_calls = total_calls + 1,
                            prompt_tokens = prompt_tokens + ?,
                            completion_tokens = completion_tokens + ?,
                            last_call_at = ?
                        WHERE project_id = ?""",
                        (prompt_tokens, completion_tokens, now, project_id),
                    )
                conn.commit()
            finally:
                conn.close()

    def get_document_count(self, project_id: str, vector_store=None) -> int:
        """获取项目文档数量（由外部传入 vector_store 查询）。"""
        if vector_store and hasattr(vector_store, "count"):
            try:
                return vector_store.count(project_id=project_id)
            except Exception:
                pass
        return 0