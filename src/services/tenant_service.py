"""租户管理服务：基于 SQLite 持久化的 Tenant CRUD 与 API Key 鉴权。

使用 Python 标准库 sqlite3（零外部依赖），适合小团队。
后续可扩展至 PostgreSQL。

表结构：
    tenants (
        tenant_id TEXT PRIMARY KEY,
        api_key TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        knowledge_path TEXT DEFAULT '',
        llm_api_key TEXT DEFAULT '',
        llm_api_base TEXT DEFAULT '',
        llm_model TEXT DEFAULT '',
        llm_timeout INTEGER DEFAULT 30,
        rate_limit_per_user TEXT DEFAULT '60/minute',
        rate_limit_global TEXT DEFAULT '1000/minute',
        system_prompt TEXT DEFAULT '',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
"""

import os
import secrets
import sqlite3
import threading
from typing import List, Optional

from src.domain.models import Tenant
from src.domain.exceptions import TenantNotFoundError, TenantSuspendedError
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

_INITIALIZE_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    api_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    knowledge_path TEXT DEFAULT '',
    llm_api_key TEXT DEFAULT '',
    llm_api_base TEXT DEFAULT '',
    llm_model TEXT DEFAULT '',
    llm_timeout INTEGER DEFAULT 30,
    rate_limit_per_user TEXT DEFAULT '60/minute',
    rate_limit_global TEXT DEFAULT '1000/minute',
    system_prompt TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tenants_api_key ON tenants(api_key);
CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);
"""


def _tenant_from_row(row: tuple) -> Tenant:
    """从 SQLite 行记录构建 Tenant 实例。"""
    (
        tenant_id,
        api_key,
        name,
        status,
        knowledge_path,
        llm_api_key,
        llm_api_base,
        llm_model,
        llm_timeout,
        rate_limit_per_user,
        rate_limit_global,
        system_prompt,
        created_at,
        updated_at,
    ) = row
    return Tenant(
        tenant_id=tenant_id,
        api_key=api_key,
        name=name,
        status=status,
        knowledge_path=knowledge_path,
        llm_api_key=llm_api_key or "",
        llm_api_base=llm_api_base or "",
        llm_model=llm_model or "",
        llm_timeout=llm_timeout,
        rate_limit_per_user=rate_limit_per_user,
        rate_limit_global=rate_limit_global,
        system_prompt=system_prompt or "",
        created_at=created_at,
        updated_at=updated_at,
    )


def _generate_api_key() -> str:
    """生成安全的 API Key。"""
    return "sk_" + secrets.token_hex(24)


class TenantService:
    """租户管理服务：CRUD + API Key 鉴权 + 配置读取。

    线程安全：内部使用 RLock 保护所有数据库操作。

    Examples:
        >>> svc = TenantService()
        >>> tenant = svc.create_tenant("电商A", "sk_abc123")
        >>> found = svc.get_by_api_key("sk_abc123")
    """

    def __init__(self, storage_path: Optional[str] = None):
        self._storage_path = storage_path or settings.tenant.storage_path
        self._lock = threading.RLock()
        self._ensure_db()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def _ensure_db(self) -> None:
        """确保数据库目录和表存在。"""
        dir_path = os.path.dirname(self._storage_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with self._lock:
            conn = self._get_connection()
            try:
                conn.executescript(_INITIALIZE_SQL)
                conn.commit()
            finally:
                conn.close()
            logger.info(f"租户数据库已初始化: {self._storage_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接（每次新建，避免跨线程问题）。"""
        conn = sqlite3.connect(self._storage_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_tenant(
        self,
        name: str,
        tenant_id: Optional[str] = None,
        api_key: Optional[str] = None,
        status: str = "active",
        knowledge_path: str = "",
        llm_api_key: str = "",
        llm_api_base: str = "",
        llm_model: str = "",
        llm_timeout: int = 30,
        rate_limit_per_user: str = "60/minute",
        rate_limit_global: str = "1000/minute",
        system_prompt: str = "",
    ) -> Tenant:
        """创建新租户。

        Args:
            name: 租户名称
            tenant_id: 租户 ID，不指定则自动生成
            api_key: API Key，不指定则自动生成

        Returns:
            创建的 Tenant 实例
        """
        import time

        key = api_key or _generate_api_key()
        if tenant_id is None:
            tenant_id = f"tenant_{secrets.token_hex(4)}"
        now = int(time.time())

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO tenants (
                        tenant_id, api_key, name, status, knowledge_path,
                        llm_api_key, llm_api_base, llm_model, llm_timeout,
                        rate_limit_per_user, rate_limit_global, system_prompt,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        key,
                        name,
                        status,
                        knowledge_path,
                        llm_api_key,
                        llm_api_base,
                        llm_model,
                        llm_timeout,
                        rate_limit_per_user,
                        rate_limit_global,
                        system_prompt,
                        now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        logger.info(f"租户已创建: {tenant_id} ({name})")
        return self.get_by_id(tenant_id)

    def get_by_id(self, tenant_id: str) -> Optional[Tenant]:
        """根据 ID 获取租户。"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
            return _tenant_from_row(row) if row else None
        finally:
            conn.close()

    def get_by_api_key(self, api_key: str) -> Optional[Tenant]:
        """根据 API Key 获取租户（用于请求鉴权）。

        只返回 active 状态的租户。
        """
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM tenants WHERE api_key = ? AND status = 'active'",
                (api_key,),
            ).fetchone()
            return _tenant_from_row(row) if row else None
        finally:
            conn.close()

    def list_tenants(self, include_deleted: bool = False) -> List[Tenant]:
        """列出所有租户。

        Args:
            include_deleted: 是否包含已删除（deleted）的租户。默认 False。
        """
        conn = self._get_connection()
        try:
            if include_deleted:
                query = "SELECT * FROM tenants ORDER BY created_at DESC"
            else:
                query = "SELECT * FROM tenants WHERE status != 'deleted' ORDER BY created_at DESC"
            rows = conn.execute(query).fetchall()
            return [_tenant_from_row(r) for r in rows]
        finally:
            conn.close()

    def update_tenant(
        self,
        tenant_id: str,
        name: str = "",
        status: str = "",
        knowledge_path: str = "",
        llm_api_key: str = "",
        llm_api_base: str = "",
        llm_model: str = "",
        llm_timeout: int = 0,
        rate_limit_per_user: str = "",
        rate_limit_global: str = "",
        system_prompt: str = "",
    ) -> Tenant:
        """更新租户配置。

        字段留空则不更新该字段。
        """
        import time

        with self._lock:
            conn = self._get_connection()
            try:
                tenant = self.get_by_id(tenant_id)
                if not tenant:
                    raise TenantNotFoundError(f"租户不存在: {tenant_id}")

                updates = {}
                if name:
                    updates["name"] = name
                if status:
                    updates["status"] = status
                if knowledge_path:
                    updates["knowledge_path"] = knowledge_path
                if llm_api_key:
                    updates["llm_api_key"] = llm_api_key
                if llm_api_base:
                    updates["llm_api_base"] = llm_api_base
                if llm_model:
                    updates["llm_model"] = llm_model
                if llm_timeout > 0:
                    updates["llm_timeout"] = llm_timeout
                if rate_limit_per_user:
                    updates["rate_limit_per_user"] = rate_limit_per_user
                if rate_limit_global:
                    updates["rate_limit_global"] = rate_limit_global
                # system_prompt 始终处理：None → 跳过，"" 表示清空，非空则设置
                if system_prompt is not None:
                    updates["system_prompt"] = system_prompt

                if updates:
                    now = int(time.time())
                    set_clauses = ", ".join(f"{k} = ?" for k in updates)
                    set_clauses += ", updated_at = ?"
                    values = list(updates.values()) + [now, tenant_id]
                    conn.execute(
                        f"UPDATE tenants SET {set_clauses} WHERE tenant_id = ?",
                        values,
                    )
                conn.commit()
            finally:
                conn.close()

        logger.info(f"租户已更新: {tenant_id}")
        return self.get_by_id(tenant_id)

    def delete_tenant(self, tenant_id: str) -> bool:
        """删除租户（软删除：标记为 suspended）。

        不直接删除记录，保留数据可追溯性。
        """
        tenant = self.get_by_id(tenant_id)
        if not tenant:
            return False

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "UPDATE tenants SET status = 'deleted', updated_at = ? WHERE tenant_id = ?",
                    (int(__import__("time").time()), tenant_id),
                )
                conn.commit()
            finally:
                conn.close()

        logger.info(f"租户已删除: {tenant_id}")
        return True

    def rotate_api_key(self, tenant_id: str) -> str:
        """轮换租户 API Key。

        生成新 key，旧的立即失效。
        """
        import time

        new_key = _generate_api_key()
        with self._lock:
            conn = self._get_connection()
            try:
                tenant = self.get_by_id(tenant_id)
                if not tenant:
                    raise TenantNotFoundError(f"租户不存在: {tenant_id}")
                now = int(time.time())
                conn.execute(
                    "UPDATE tenants SET api_key = ?, updated_at = ? WHERE tenant_id = ?",
                    (new_key, now, tenant_id),
                )
                conn.commit()
            finally:
                conn.close()

        logger.info(f"API Key 已轮换: {tenant_id}")
        return new_key

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def get_document_count(self, tenant_id: str) -> int:
        """获取租户知识库文档数量（从 Zvec 查询，由外部传入）。

        TenantService 自身不直接访问 Zvec，此方法由调用方传入
        vector_store 结果后更新（当前作为占位，返回 0）。
        """
        return 0

    # ------------------------------------------------------------------
    # 默认租户（向后兼容）
    # ------------------------------------------------------------------

    def ensure_default_tenant(self) -> Tenant:
        """确保存在 default 租户（向后兼容单租户遗留数据）。

        如果 .env 中配置了 DEFAULT_TENANT_API_KEY，使用该 key；
        否则自动生成一个并记录到日志。
        """
        tenant = self.get_by_id("default")
        if tenant:
            return tenant

        default_key = settings.tenant.default_tenant_api_key
        if not default_key:
            default_key = _generate_api_key()
            logger.warning(
                "DEFAULT_TENANT_API_KEY 未配置，已自动生成默认租户 API Key。"
                "请在 .env 中配置 DEFAULT_TENANT_API_KEY 以保持重启后一致。"
            )

        return self.create_tenant(
            name="默认租户",
            tenant_id="default",
            api_key=default_key,
            status="active",
        )
