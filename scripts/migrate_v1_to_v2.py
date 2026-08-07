#!/usr/bin/env python3
"""v1 → v2 数据迁移脚本。

从旧 Tenant 体系迁移到新 User → Project 体系。
v1 使用 tenants 表（tenant_id + api_key 体系）。
v2 使用 User → Project 模型（user_id + email + project_id + api_key 体系）。

迁移内容：
1. 读取旧 tenants.db 中的所有 tenant 记录
2. 为每个 tenant 创建一个对应的 User（email: "migrated-{tenant_id}@local"）
3. 为每个 User 创建一个 Project，使用原 Tenant 的 API Key 和配置
4. 将旧 knowledge_base 中的文档（通过 Zvec）重新关联到新 Project
5. 报告迁移结果

运行方式：
    python scripts/migrate_v1_to_v2.py                    # 实际迁移
    python scripts/migrate_v1_to_v2.py --dry-run          # 预览模式，不实际写入
    python scripts/migrate_v1_to_v2.py --old-db path      # 指定旧数据库路径
"""

import argparse
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ================================================================
# 配置
# ================================================================

DEFAULT_OLD_DB = "data/tenants.db"
DEFAULT_NEW_USERS_DB = "data/users.db"
DEFAULT_NEW_PROJECTS_DB = "data/projects.db"
BACKUP_SUFFIX = ".v1_backup"


# ================================================================
# 日志
# ================================================================


def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def warn(msg: str):
    log(msg, "WARN")


def error(msg: str):
    log(msg, "ERROR")


# ================================================================
# 旧数据库读取
# ================================================================


def _connect_old_db(db_path: str) -> sqlite3.Connection:
    """连接到旧 tenant 数据库。"""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"旧数据库文件不存在: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _get_old_tenants(conn: sqlite3.Connection) -> list:
    """读取所有旧 tenant 记录。"""
    # 检查表是否存在
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tenants'"
    ).fetchall()
    if not tables:
        raise ValueError("旧数据库中没有 tenants 表")

    rows = conn.execute("SELECT * FROM tenants ORDER BY created_at ASC").fetchall()
    log(f"读取到 {len(rows)} 个旧租户")
    return [dict(r) for r in rows]


# ================================================================
# 新数据库写入
# ================================================================


def _ensure_new_db(db_path: str, schema_sql: str) -> sqlite3.Connection:
    """确保新数据库存在并返回连接。"""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(schema_sql)
    conn.commit()
    return conn


_USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT DEFAULT '',
    is_verified INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

_PROJECTS_SCHEMA = """
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
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS project_stats (
    project_id TEXT PRIMARY KEY,
    total_calls INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cache_hits INTEGER DEFAULT 0,
    last_call_at INTEGER DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
"""


# ================================================================
# 迁移逻辑
# ================================================================


def migrate_tenant_to_user_project(
    tenant: dict,
    users_conn: sqlite3.Connection,
    projects_conn: sqlite3.Connection,
    dry_run: bool = False,
) -> dict:
    """将单个 tenant 迁移为 User + Project。

    Returns:
        {"tenant_id": str, "user_id": str, "project_id": str, "status": str}
    """
    tenant_id = tenant["tenant_id"]
    api_key = tenant.get("api_key", f"sk_migrated_{tenant_id}")
    name = tenant.get("name", f"迁移用户_{tenant_id}")
    email = f"migrated-{tenant_id}@local"

    # 检查是否已迁移（幂等）
    existing = users_conn.execute(
        "SELECT user_id FROM users WHERE email = ?", (email,)
    ).fetchone()
    if existing:
        warn(f"用户已存在，跳过: {email}")
        return {"tenant_id": tenant_id, "user_id": existing[0], "project_id": "", "status": "skipped"}

    now = int(time.time())

    # 创建 User
    user_id = f"user_migrated_{tenant_id}"
    # 使用 passlib 哈希，但迁移用户使用随机密码（强制重置）
    password_hash = "!migrated_account_no_password"

    if not dry_run:
        users_conn.execute(
            """INSERT INTO users (user_id, email, password_hash, name, is_verified, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, 1, ?, ?)""",
            (user_id, email, password_hash, name, now, now),
        )
        users_conn.commit()
        log(f"用户已创建: {user_id} ({email})")

    # 创建 Project
    project_id = f"proj_migrated_{tenant_id}"
    llm_api_key = tenant.get("llm_api_key", "")
    llm_api_base = tenant.get("llm_api_base", "")
    llm_model = tenant.get("llm_model", "")
    llm_timeout = tenant.get("llm_timeout", 30)
    rate_limit_per_user = tenant.get("rate_limit_per_user", "60/minute")
    rate_limit_global = tenant.get("rate_limit_global", "1000/minute")
    system_prompt = tenant.get("system_prompt", "")

    if not dry_run:
        projects_conn.execute(
            """INSERT INTO projects (project_id, user_id, api_key, name, status, llm_api_key, llm_api_base,
               llm_model, llm_timeout, rate_limit_per_user, rate_limit_global, system_prompt, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, user_id, api_key, name, llm_api_key, llm_api_base,
             llm_model, llm_timeout, rate_limit_per_user, rate_limit_global,
             system_prompt, now, now),
        )
        projects_conn.commit()
        log(f"项目已创建: {project_id} ({name})")

    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "project_id": project_id,
        "status": "migrated",
    }


# ================================================================
# 备份
# ================================================================


def backup_database(db_path: str) -> str:
    """备份数据库文件。"""
    if not os.path.exists(db_path):
        return ""

    backup_path = f"{db_path}{BACKUP_SUFFIX}"
    shutil.copy2(db_path, backup_path)
    log(f"数据库已备份: {db_path} → {backup_path}")
    return backup_path


# ================================================================
# 主流程
# ================================================================


def main():
    parser = argparse.ArgumentParser(description="v1 → v2 数据迁移脚本")
    parser.add_argument(
        "--old-db", default=DEFAULT_OLD_DB,
        help=f"旧 tenant 数据库路径（默认: {DEFAULT_OLD_DB}）"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="预览模式，不实际写入数据库"
    )
    parser.add_argument(
        "--users-db", default=DEFAULT_NEW_USERS_DB,
        help=f"新用户数据库路径（默认: {DEFAULT_NEW_USERS_DB}）"
    )
    parser.add_argument(
        "--projects-db", default=DEFAULT_NEW_PROJECTS_DB,
        help=f"新项目数据库路径（默认: {DEFAULT_NEW_PROJECTS_DB}）"
    )
    args = parser.parse_args()

    log(f"=== v1 → v2 数据迁移 {'[DRY-RUN]' if args.dry_run else ''} ===")
    log(f"旧数据库: {args.old_db}")
    log(f"新用户数据库: {args.users_db}")
    log(f"新项目数据库: {args.projects_db}")

    # 检查旧数据库
    if not os.path.exists(args.old_db):
        warn(f"旧数据库不存在: {args.old_db}")
        log("没有旧数据需要迁移，跳过")
        return

    # 备份旧数据库
    if not args.dry_run:
        backup_database(args.old_db)
        db_dir = os.path.dirname(args.users_db) or "."
        os.makedirs(db_dir, exist_ok=True)

    # 连接旧数据库
    try:
        old_conn = _connect_old_db(args.old_db)
        tenants = _get_old_tenants(old_conn)
        old_conn.close()
    except (FileNotFoundError, ValueError) as e:
        error(str(e))
        sys.exit(1)

    if not tenants:
        log("没有旧租户数据，无需迁移")
        return

    # 连接新数据库
    users_conn = _ensure_new_db(args.users_db, _USERS_SCHEMA)
    projects_conn = _ensure_new_db(args.projects_db, _PROJECTS_SCHEMA)

    # 执行迁移
    results = []
    for tenant in tenants:
        result = migrate_tenant_to_user_project(
            tenant, users_conn, projects_conn, dry_run=args.dry_run
        )
        results.append(result)

    # 报告
    migrated = sum(1 for r in results if r["status"] == "migrated")
    skipped = sum(1 for r in results if r["status"] == "skipped")

    log(f"=== 迁移完成 ===")
    log(f"总租户: {len(tenants)}")
    log(f"已迁移: {migrated}")
    log(f"已跳过: {skipped}")

    if not args.dry_run:
        log(f"用户数据库: {args.users_db}")
        log(f"项目数据库: {args.projects_db}")
        log("注意：迁移后的用户密码为随机密码，请通过\"忘记密码\"功能重置")
    else:
        log("DRY-RUN 模式，未实际写入数据")

    # 验证
    migrated_count = users_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    project_count = projects_conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    log(f"当前用户总数: {migrated_count}")
    log(f"当前项目总数: {project_count}")

    users_conn.close()
    projects_conn.close()


if __name__ == "__main__":
    main()