#!/usr/bin/env python3
"""
单租户 → 多租户数据迁移脚本。

用途：
  现有 data/zvec/ 没有 tenant_id 字段，需要迁移到新 schema。

流程：
  1. 直接用 zvec.open() 打开旧 collection（旧 schema，无 tenant_id）
  2. 遍历所有文档，读取字段
  3. 用新 ZvecStore（含 tenant_id 字段的新 schema）创建新 collection
  4. 将旧文档（带 tenant_id='default'）插入新 collection（异步）
  5. 将新目录替换旧目录
  6. 在 tenant_service 中创建 default 租户

用法：
  python scripts/migrate_single_to_multi_tenant.py

注意：
  - 迁移前会自动备份旧目录到 data/zvec.migration_backup_{timestamp}
"""

import asyncio
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import zvec

from src.domain.models import DEFAULT_TENANT_ID, Document
from src.infrastructure.embedding_service import SentenceBertEmbeddingService
from src.infrastructure.zvec_store import ZvecStore
from src.services.tenant_service import TenantService
from src.utils.logger import get_logger

logger = get_logger(__name__)

SOURCE_PATH = "data/zvec"
BACKUP_DIR = f"data/zvec.migration_backup_{int(time.time())}"
NEW_PATH = "data/zvec_new"


def _read_old_docs(tmp_old: str) -> list[dict]:
    """直接读取旧 collection 的文档（不使用 ZvecStore，避免 schema 冲突）。"""
    coll = zvec.open(tmp_old)
    count = coll.stats.doc_count
    docs = []
    for row in coll.query(topk=max(1000, count)):
        docs.append({
            "doc_id": row.fields.get("doc_id", row.id),
            "content": row.fields.get("content", ""),
            "title": row.fields.get("title", ""),
            "tags": row.fields.get("tags", []),
            "source": row.fields.get("source", ""),
            "created_at": row.fields.get("created_at", 0),
        })
    return docs, count


async def _migrate(old_docs: list[dict]):
    """用新 schema 插入旧文档（异步）。"""
    embedder = SentenceBertEmbeddingService()
    new_store = ZvecStore(data_path=NEW_PATH)

    migrated = 0
    failed = 0

    for row in old_docs:
        try:
            doc = Document(
                doc_id=row["doc_id"],
                content=row["content"],
                title=row["title"],
                tags=row["tags"],
                source=row["source"],
                tenant_id=DEFAULT_TENANT_ID,
                created_at=row["created_at"],
            )
            vec = await embedder.encode(row["content"])
            await new_store.ainsert(doc, vec, tenant_id=DEFAULT_TENANT_ID)
            migrated += 1
            if migrated % 10 == 0:
                logger.info(f"已迁移 {migrated} / {len(old_docs)}")
        except Exception as e:
            logger.error(f"迁移失败: {row['doc_id']} | {e}")
            failed += 1

    # 不调用 close/aclose — ZvecStore.close() 会调用 destroy() 删除数据目录
    # 进程退出时会自动释放文件句柄
    logger.info(f"迁移完成: {migrated} 成功, {failed} 失败")
    return migrated, failed


def migrate():
    if not os.path.exists(SOURCE_PATH):
        logger.warning(f"旧 Zvec 目录不存在: {SOURCE_PATH}，无需迁移")
        return

    # 1. 备份
    shutil.copytree(SOURCE_PATH, BACKUP_DIR)
    logger.info(f"旧目录已备份: {BACKUP_DIR}")

    tmp_old = "data/zvec_old_tmp"
    if os.path.exists(tmp_old):
        shutil.rmtree(tmp_old)
    shutil.move(SOURCE_PATH, tmp_old)
    logger.info(f"旧目录已临时改名: {tmp_old}")

    try:
        # 2. 读取旧文档
        old_docs, count = _read_old_docs(tmp_old)
        logger.info(f"旧 collection 共 {count} 篇文档，读取了 {len(old_docs)} 篇")

        # 3. 清理临时旧目录
        if os.path.exists(tmp_old):
            shutil.rmtree(tmp_old)

        # 4. 用新 schema 创建新 collection 并写入
        if os.path.exists(NEW_PATH):
            shutil.rmtree(NEW_PATH)

        if not old_docs:
            # 无文档，直接创建空 collection
            ZvecStore(data_path=NEW_PATH).close()
        else:
            migrated, failed = asyncio.run(_migrate(old_docs))

        # 5. 替换目录
        if os.path.exists(SOURCE_PATH):
            shutil.rmtree(SOURCE_PATH)
        shutil.move(NEW_PATH, SOURCE_PATH)
        logger.info(f"新库已替换旧目录: {SOURCE_PATH}")

        # 6. 创建 default 租户（独立于迁移，即使失败也不回滚 zvec）
        try:
            tenant_svc = TenantService()
            tenant_svc.ensure_default_tenant()
            logger.info("default 租户已确保存在")
        except Exception as e:
            logger.warning(f"创建 default 租户失败（不影响迁移）: {e}")
            logger.warning("请在 .env 中配置 DEFAULT_TENANT_API_KEY 后手动执行: python -c \"from src.services.tenant_service import TenantService; TenantService().ensure_default_tenant()\"")

        logger.info("迁移成功完成！")

    except Exception as e:
        logger.error(f"迁移失败: {e}", exc_info=True)
        # 回滚：只有旧目录还在且新目录不存在时才回滚
        new_exists = os.path.exists(SOURCE_PATH)
        old_exists = os.path.exists(tmp_old)
        if new_exists:
            shutil.rmtree(SOURCE_PATH)
        if old_exists:
            shutil.move(tmp_old, SOURCE_PATH)
            logger.info(f"已回滚到旧目录: {SOURCE_PATH}")
        logger.info(f"备份保留在: {BACKUP_DIR}")
        raise


if __name__ == "__main__":
    migrate()
