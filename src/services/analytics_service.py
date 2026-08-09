"""分析服务：问答日志、趋势、热门问题、反馈、缺口分析、转人工请求。

提供完整的运营数据能力：
- 日志记录与查询（含 conversation_id 支持会话追溯）
- 问答量趋势
- 热门问题排行
- 满意度反馈
- 日志导出
- 知识库缺口分析
- 人工客服转接请求
"""

import csv
import io
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import List, Optional

from src.services.plan_service import PlanService
from src.services.project_service import ProjectService
from src.utils.logger import get_logger

logger = get_logger(__name__)

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS chat_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    conversation_id TEXT DEFAULT '',
    query TEXT NOT NULL,
    answer TEXT NOT NULL,
    sources TEXT DEFAULT '[]',
    cache_hit INTEGER DEFAULT 0,
    llm_used INTEGER DEFAULT 1,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_logs_project ON chat_logs(project_id);
CREATE INDEX IF NOT EXISTS idx_chat_logs_created ON chat_logs(created_at);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    rating TEXT NOT NULL CHECK(rating IN ('good', 'bad')),
    created_at INTEGER NOT NULL,
    FOREIGN KEY (log_id) REFERENCES chat_logs(id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_project ON feedback(project_id);

CREATE TABLE IF NOT EXISTS handoff_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    conversation_id TEXT DEFAULT '',
    query TEXT NOT NULL,
    contact_email TEXT DEFAULT '',
    contact_phone TEXT DEFAULT '',
    note TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    resolved_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_handoff_project ON handoff_requests(project_id);
CREATE INDEX IF NOT EXISTS idx_handoff_status ON handoff_requests(status);
"""


class AnalyticsService:
    """分析服务：日志、趋势、反馈、缺口、转人工。"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or "data/analytics.db"
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
                # 兼容旧库：为 chat_logs 补充 conversation_id 列
                self._migrate(conn)
                conn.commit()
            finally:
                conn.close()
            logger.info(f"分析数据库已初始化: {self._db_path}")

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """旧库迁移：补加新增列和索引（幂等）。"""
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(chat_logs)").fetchall()}
        if "conversation_id" not in columns:
            conn.execute("ALTER TABLE chat_logs ADD COLUMN conversation_id TEXT DEFAULT ''")
            logger.info("chat_logs 表迁移：新增 conversation_id 列")
        # 确保 conversation_id 索引存在（列已存在或刚添加）
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_logs_conv ON chat_logs(conversation_id)")
        except Exception:
            pass

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ================================================================
    # 日志
    # ================================================================

    def record_chat(
        self,
        project_id: str,
        query: str,
        answer: str,
        sources: Optional[List[dict]] = None,
        cache_hit: bool = False,
        llm_used: bool = True,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        conversation_id: str = "",
    ) -> int:
        """记录一次问答。返回 log_id。"""
        now = int(datetime.now(timezone.utc).timestamp())
        sources_json = json.dumps(sources or [], ensure_ascii=False)
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    """INSERT INTO chat_logs
                    (project_id, conversation_id, query, answer, sources, cache_hit, llm_used,
                     prompt_tokens, completion_tokens, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (project_id, conversation_id, query, answer, sources_json,
                     int(cache_hit), int(llm_used),
                     prompt_tokens, completion_tokens, now),
                )
                conn.commit()
                log_id = cur.lastrowid

                # 同步记录月度用量与项目统计（不影响主流程）
                try:
                    PlanService().record_usage(
                        project_id=project_id,
                        call_count=1,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )
                    ProjectService().record_call(
                        project_id=project_id,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        cache_hit=cache_hit,
                    )
                except Exception as e:
                    logger.warning(f"记录用量失败: {e}")

                return log_id
            finally:
                conn.close()

    def get_logs(
        self,
        project_id: str,
        page: int = 1,
        page_size: int = 20,
        search: str = "",
        start_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> dict:
        """分页查询问答日志。"""
        conditions = ["project_id = ?"]
        params = [project_id]

        if search:
            conditions.append("query LIKE ?")
            params.append(f"%{search}%")
        if start_date:
            conditions.append("created_at >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("created_at <= ?")
            params.append(end_date)

        where = " AND ".join(conditions)
        offset = (page - 1) * page_size

        conn = self._get_connection()
        try:
            count_row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM chat_logs WHERE {where}", params
            ).fetchone()
            total = count_row["cnt"] if count_row else 0

            rows = conn.execute(
                f"SELECT * FROM chat_logs WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()

            items = []
            for r in rows:
                items.append({
                    "id": r["id"],
                    "project_id": r["project_id"],
                    "conversation_id": r["conversation_id"] if "conversation_id" in r.keys() else "",
                    "query": r["query"],
                    "answer": r["answer"],
                    "sources": json.loads(r["sources"]) if r["sources"] else [],
                    "cache_hit": bool(r["cache_hit"]),
                    "llm_used": bool(r["llm_used"]),
                    "prompt_tokens": r["prompt_tokens"],
                    "completion_tokens": r["completion_tokens"],
                    "created_at": r["created_at"],
                })

            return {"items": items, "total": total, "page": page, "page_size": page_size}
        finally:
            conn.close()

    def delete_logs(self, project_id: str) -> int:
        """删除项目的所有问答日志。返回删除的条数。"""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute("DELETE FROM chat_logs WHERE project_id = ?", (project_id,))
                conn.commit()
                count = cur.rowcount
                logger.info(f"问答日志已清空: project={project_id} deleted={count}")
                return count
            finally:
                conn.close()

    def delete_log(self, log_id: int, project_id: str) -> bool:
        """删除单条问答日志。返回是否删除了记录。"""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    "DELETE FROM chat_logs WHERE id = ? AND project_id = ?",
                    (log_id, project_id),
                )
                conn.commit()
                deleted = cur.rowcount > 0
                if deleted:
                    logger.info(f"问答日志已删除: log_id={log_id} project={project_id}")
                return deleted
            finally:
                conn.close()

    def delete_logs_batch(self, log_ids: List[int], project_id: str) -> int:
        """批量删除指定日志。返回删除的条数。"""
        if not log_ids:
            return 0
        placeholders = ",".join("?" * len(log_ids))
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    f"DELETE FROM chat_logs WHERE id IN ({placeholders}) AND project_id = ?",
                    (*log_ids, project_id),
                )
                conn.commit()
                count = cur.rowcount
                logger.info(f"问答日志批量删除: project={project_id} deleted={count}")
                return count
            finally:
                conn.close()

    def export_logs(
        self,
        project_id: str,
        format: str = "csv",
        start_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> str:
        """导出问答日志。"""
        conditions = ["project_id = ?"]
        params = [project_id]
        if start_date:
            conditions.append("created_at >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("created_at <= ?")
            params.append(end_date)

        where = " AND ".join(conditions)
        conn = self._get_connection()
        try:
            rows = conn.execute(
                f"SELECT * FROM chat_logs WHERE {where} ORDER BY created_at DESC",
                params,
            ).fetchall()

            if format == "csv":
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(["ID", "时间", "会话ID", "问题", "回答", "缓存命中", "使用LLM", "输入Token", "输出Token"])
                for r in rows:
                    writer.writerow([
                        r["id"],
                        datetime.fromtimestamp(r["created_at"], tz=timezone.utc).isoformat(),
                        r["conversation_id"] if "conversation_id" in r.keys() else "",
                        r["query"],
                        r["answer"],
                        "是" if r["cache_hit"] else "否",
                        "是" if r["llm_used"] else "否",
                        r["prompt_tokens"],
                        r["completion_tokens"],
                    ])
                return output.getvalue()
            else:
                items = []
                for r in rows:
                    items.append({
                        "id": r["id"],
                        "query": r["query"],
                        "answer": r["answer"],
                        "cache_hit": bool(r["cache_hit"]),
                        "created_at": r["created_at"],
                    })
                return json.dumps(items, ensure_ascii=False, indent=2)
        finally:
            conn.close()

    # ================================================================
    # 趋势分析
    # ================================================================

    def get_trends(
        self,
        project_id: str,
        days: int = 30,
    ) -> List[dict]:
        """获取每日问答量趋势。"""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """SELECT date(datetime(created_at, 'unixepoch')) as day,
                          COUNT(*) as calls,
                          SUM(prompt_tokens) as prompt_tokens,
                          SUM(completion_tokens) as completion_tokens,
                          SUM(cache_hit) as cache_hits
                   FROM chat_logs
                   WHERE project_id = ?
                     AND created_at >= ?
                   GROUP BY day
                   ORDER BY day ASC""",
                (project_id, int(datetime.now(timezone.utc).timestamp()) - days * 86400),
            ).fetchall()

            return [
                {
                    "date": r["day"],
                    "calls": r["calls"],
                    "prompt_tokens": r["prompt_tokens"] or 0,
                    "completion_tokens": r["completion_tokens"] or 0,
                    "cache_hits": r["cache_hits"] or 0,
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_top_questions(
        self,
        project_id: str,
        limit: int = 10,
        days: int = 30,
    ) -> List[dict]:
        """获取热门问题 Top N。"""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """SELECT query, COUNT(*) as count,
                          SUM(cache_hit) as cache_hits
                   FROM chat_logs
                   WHERE project_id = ?
                     AND created_at >= ?
                   GROUP BY query
                   ORDER BY count DESC
                   LIMIT ?""",
                (project_id, int(datetime.now(timezone.utc).timestamp()) - days * 86400, limit),
            ).fetchall()

            return [
                {
                    "query": r["query"],
                    "count": r["count"],
                    "cache_hits": r["cache_hits"] or 0,
                }
                for r in rows
            ]
        finally:
            conn.close()

    # ================================================================
    # 反馈
    # ================================================================

    def record_feedback(self, log_id: int, project_id: str, rating: str) -> None:
        """记录用户反馈。"""
        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "INSERT INTO feedback (log_id, project_id, rating, created_at) VALUES (?, ?, ?, ?)",
                    (log_id, project_id, rating, now),
                )
                conn.commit()
            finally:
                conn.close()

    def get_satisfaction(self, project_id: str, days: int = 30) -> dict:
        """获取满意度统计。"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT
                          COUNT(*) as total,
                          SUM(CASE WHEN rating = 'good' THEN 1 ELSE 0 END) as good,
                          SUM(CASE WHEN rating = 'bad' THEN 1 ELSE 0 END) as bad
                   FROM feedback
                   WHERE project_id = ?
                     AND created_at >= ?""",
                (project_id, int(datetime.now(timezone.utc).timestamp()) - days * 86400),
            ).fetchone()

            if not row or row["total"] == 0:
                return {"total": 0, "good": 0, "bad": 0, "satisfaction_rate": 0.0}

            total = row["total"]
            good = row["good"] or 0
            return {
                "total": total,
                "good": good,
                "bad": row["bad"] or 0,
                "satisfaction_rate": round(good / total * 100, 1),
            }
        finally:
            conn.close()

    # ================================================================
    # 知识库缺口分析
    # ================================================================

    def get_gaps(
        self,
        project_id: str,
        days: int = 30,
        limit: int = 20,
    ) -> dict:
        """分析知识库缺口：AI 答不上来的问题。

        识别两类缺口：
        1. 无来源问题：检索未命中任何文档（sources 为空）
        2. 差评问题：用户明确标记为不满意（bad rating）

        返回按出现次数排序的缺口列表。
        """
        since = int(datetime.now(timezone.utc).timestamp()) - days * 86400
        conn = self._get_connection()
        try:
            # 1. 无来源问题（检索命中 0 篇文档）
            no_source_rows = conn.execute(
                """SELECT
                          query,
                          COUNT(*) as count,
                          MAX(created_at) as last_seen
                   FROM chat_logs
                   WHERE project_id = ?
                     AND created_at >= ?
                     AND (sources IS NULL OR sources = '[]')
                   GROUP BY query
                   ORDER BY count DESC
                   LIMIT ?""",
                (project_id, since, max(1, limit // 2)),
            ).fetchall()

            no_source = [
                {
                    "query": r["query"],
                    "count": r["count"],
                    "last_seen": r["last_seen"],
                    "type": "no_source",
                    "reason": "检索未命中任何文档",
                }
                for r in no_source_rows
            ]

            # 2. 差评问题（用户标记 bad）
            bad_rows = conn.execute(
                """SELECT
                          cl.query,
                          COUNT(*) as count,
                          MAX(cl.created_at) as last_seen
                   FROM feedback fb
                   JOIN chat_logs cl ON fb.log_id = cl.id
                   WHERE fb.project_id = ?
                     AND fb.rating = 'bad'
                     AND fb.created_at >= ?
                   GROUP BY cl.query
                   ORDER BY count DESC
                   LIMIT ?""",
                (project_id, since, max(1, limit // 2)),
            ).fetchall()

            bad = [
                {
                    "query": r["query"],
                    "count": r["count"],
                    "last_seen": r["last_seen"],
                    "type": "bad_feedback",
                    "reason": "用户反馈不满意",
                }
                for r in bad_rows
            ]

            # 合并去重（同一问题可能同时属于两类）
            seen = set()
            gaps = []
            for g in no_source + bad:
                if g["query"] in seen:
                    continue
                seen.add(g["query"])
                gaps.append(g)

            return {
                "items": gaps[:limit],
                "total": len(gaps),
                "days": days,
            }
        finally:
            conn.close()

    # ================================================================
    # 人工客服转接
    # ================================================================

    def record_handoff(
        self,
        project_id: str,
        query: str,
        conversation_id: str = "",
        contact_email: str = "",
        contact_phone: str = "",
        note: str = "",
    ) -> int:
        """记录人工客服转接请求。返回请求 ID。"""
        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    """INSERT INTO handoff_requests
                    (project_id, conversation_id, query, contact_email, contact_phone, note, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                    (project_id, conversation_id, query, contact_email, contact_phone, note, now),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def list_handoffs(
        self,
        project_id: str,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
    ) -> dict:
        """分页查询转人工请求列表。"""
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
                f"SELECT COUNT(*) as cnt FROM handoff_requests WHERE {where}", params
            ).fetchone()
            total = count_row["cnt"] if count_row else 0

            rows = conn.execute(
                f"SELECT * FROM handoff_requests WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()

            items = []
            for r in rows:
                items.append({
                    "id": r["id"],
                    "project_id": r["project_id"],
                    "conversation_id": r["conversation_id"],
                    "query": r["query"],
                    "contact_email": r["contact_email"],
                    "contact_phone": r["contact_phone"],
                    "note": r["note"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "resolved_at": r["resolved_at"],
                })

            return {"items": items, "total": total, "page": page, "page_size": page_size}
        finally:
            conn.close()

    def resolve_handoff(self, handoff_id: int) -> bool:
        """标记转人工请求为已处理。"""
        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    "UPDATE handoff_requests SET status = 'resolved', resolved_at = ? WHERE id = ?",
                    (now, handoff_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()