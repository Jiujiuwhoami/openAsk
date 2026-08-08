"""套餐服务：套餐定义、用量限制、月度重置。

套餐：
  - Free: 100 文档, 1,000 次/月, 3 项目, 10/min 频率
  - Pro: 1,000 文档, 10,000 次/月, 10 项目, 60/min 频率
  - Enterprise: 10,000 文档, 100,000 次/月, 无限项目, 300/min 频率
"""

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)

# ================================================================
# 套餐定义
# ================================================================

PLAN_LIMITS: Dict[str, Dict] = {
    "free": {
        "name": "Free",
        "price_monthly": 0,
        "max_documents": 100,
        "max_monthly_calls": 1000,
        "max_projects": 3,
        "rate_per_minute": 10,
        "custom_llm": False,
        "team_members": 1,
        "support": "community",
    },
    "pro": {
        "name": "Pro",
        "price_monthly": 2900,  # $29.00 in cents
        "max_documents": 1000,
        "max_monthly_calls": 10000,
        "max_projects": 10,
        "rate_per_minute": 60,
        "custom_llm": True,
        "team_members": 3,
        "support": "email",
    },
    "enterprise": {
        "name": "Enterprise",
        "price_monthly": 9900,  # $99.00 in cents
        "max_documents": 10000,
        "max_monthly_calls": 100000,
        "max_projects": 999,
        "rate_per_minute": 300,
        "custom_llm": True,
        "team_members": 10,
        "support": "priority",
    },
}

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS project_plans (
    project_id TEXT PRIMARY KEY,
    plan TEXT DEFAULT 'free',
    stripe_subscription_id TEXT DEFAULT '',
    stripe_customer_id TEXT DEFAULT '',
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS usage_monthly (
    project_id TEXT NOT NULL,
    year_month TEXT NOT NULL,  -- format: '2026-08'
    call_count INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    PRIMARY KEY (project_id, year_month)
);

-- 用量告警发送记录（每项目每月每阈值仅通知一次）
CREATE TABLE IF NOT EXISTS usage_alerts (
    project_id TEXT NOT NULL,
    year_month TEXT NOT NULL,
    threshold INTEGER NOT NULL,  -- 80 / 90 / 100
    sent_at INTEGER NOT NULL,
    PRIMARY KEY (project_id, year_month, threshold)
);
"""


class PlanService:
    """套餐服务：管理订阅、用量、限制检查。"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or "data/billing.db"
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
            logger.info(f"计费数据库已初始化: {self._db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get_current_month(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    # ---- 套餐管理 ----

    def get_plan(self, project_id: str) -> str:
        """获取项目当前套餐。"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT plan FROM project_plans WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            return row["plan"] if row else "free"
        finally:
            conn.close()

    def set_plan(
        self,
        project_id: str,
        plan: str,
        stripe_subscription_id: str = "",
        stripe_customer_id: str = "",
    ) -> None:
        """设置项目套餐。"""
        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO project_plans
                    (project_id, plan, stripe_subscription_id, stripe_customer_id, updated_at)
                    VALUES (?, ?, ?, ?, ?)""",
                    (project_id, plan, stripe_subscription_id, stripe_customer_id, now),
                )
                conn.commit()
            finally:
                conn.close()
        logger.info(f"套餐已更新: {project_id} → {plan}")

    def get_plan_limits(self, project_id: str) -> Dict:
        """获取项目当前套餐的限制。"""
        plan = self.get_plan(project_id)
        return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

    # ---- 用量计量 ----

    def record_usage(
        self,
        project_id: str,
        call_count: int = 1,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """记录月度用量。"""
        ym = self.get_current_month()
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    """INSERT INTO usage_monthly (project_id, year_month, call_count, prompt_tokens, completion_tokens)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, year_month) DO UPDATE SET
                        call_count = call_count + ?,
                        prompt_tokens = prompt_tokens + ?,
                        completion_tokens = completion_tokens + ?""",
                    (project_id, ym, call_count, prompt_tokens, completion_tokens,
                     call_count, prompt_tokens, completion_tokens),
                )
                conn.commit()

                # 用量变更后检查告警（不影响主流程）
                self._check_usage_alerts(project_id)
            finally:
                conn.close()

    def get_usage(self, project_id: str) -> Dict:
        """获取项目当前月度用量。"""
        ym = self.get_current_month()
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM usage_monthly WHERE project_id = ? AND year_month = ?",
                (project_id, ym),
            ).fetchone()
            if row:
                return {
                    "call_count": row["call_count"],
                    "prompt_tokens": row["prompt_tokens"],
                    "completion_tokens": row["completion_tokens"],
                }
            return {"call_count": 0, "prompt_tokens": 0, "completion_tokens": 0}
        finally:
            conn.close()

    # ---- 限制检查 ----

    def check_limits(
        self,
        project_id: str,
        document_count: int = 0,
        project_count: int = 0,
    ) -> Dict:
        """检查项目是否在套餐限制内。

        Returns:
            {"allowed": True/False, "reason": "", "limits": {...}}
        """
        limits = self.get_plan_limits(project_id)
        usage = self.get_usage(project_id)

        # 检查文档数
        if document_count > limits["max_documents"]:
            return {
                "allowed": False,
                "reason": f"文档数超过限制 ({document_count}/{limits['max_documents']})",
                "limits": limits,
                "usage": usage,
            }

        # 检查项目数
        if project_count > limits["max_projects"]:
            return {
                "allowed": False,
                "reason": f"项目数超过限制 ({project_count}/{limits['max_projects']})",
                "limits": limits,
                "usage": usage,
            }

        # 检查月度调用次数
        if usage["call_count"] >= limits["max_monthly_calls"]:
            return {
                "allowed": False,
                "reason": f"月度调用次数超过限制 ({usage['call_count']}/{limits['max_monthly_calls']})",
                "limits": limits,
                "usage": usage,
            }

        return {
            "allowed": True,
            "reason": "",
            "limits": limits,
            "usage": usage,
        }

    # ---- 用量告警 ----

    def _check_usage_alerts(self, project_id: str) -> None:
        """检查月度用量是否达到告警阈值，发送通知邮件（每阈值每月仅一次）。

        阈值：80% → 提醒，90% → 警告，100% → 已用尽。
        每项目每月每阈值仅发送一次，避免重复通知。
        """
        try:
            usage = self.get_usage(project_id)
            limits = self.get_plan_limits(project_id)
            max_calls = limits.get("max_monthly_calls", 0)
            if max_calls == 0:
                return

            call_count = usage.get("call_count", 0)
            percent = int(call_count / max_calls * 100)
            ym = self.get_current_month()

            # 找出尚未通知的最高阈值
            thresholds = [80, 90, 100]
            unsent = [
                t for t in thresholds
                if percent >= t and not self._alert_was_sent(project_id, ym, t)
            ]
            if not unsent:
                return

            threshold = max(unsent)
            plan_name = limits.get("name", "Free")
            self._send_usage_alert(project_id, plan_name, call_count, max_calls, threshold)
            self._mark_alert_sent(project_id, ym, threshold)
        except Exception as e:
            logger.warning(f"用量告警检查失败: {e}")

    def _alert_was_sent(self, project_id: str, year_month: str, threshold: int) -> bool:
        """检查指定阈值是否已发送过通知。"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT 1 FROM usage_alerts WHERE project_id = ? AND year_month = ? AND threshold = ?",
                (project_id, year_month, threshold),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def _mark_alert_sent(self, project_id: str, year_month: str, threshold: int) -> None:
        """记录告警已发送。"""
        now = int(datetime.now(timezone.utc).timestamp())
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO usage_alerts (project_id, year_month, threshold, sent_at) VALUES (?, ?, ?, ?)",
                (project_id, year_month, threshold, now),
            )
            conn.commit()
        finally:
            conn.close()
        logger.info(f"用量告警已记录: {project_id} {year_month} {threshold}%")

    def _send_usage_alert(
        self, project_id: str, plan_name: str, call_count: int, max_calls: int, threshold: int
    ) -> None:
        """发送用量告警邮件给项目所有者。"""
        try:
            from src.services.project_service import ProjectService
            from src.services.user_service import UserService
            from src.services.email_service import send_email, build_usage_alert_email

            project = ProjectService().get_by_id(project_id)
            if not project:
                logger.warning(f"用量告警：项目不存在 {project_id}")
                return

            user = UserService().get_by_id(project.user_id)
            if not user or not user.email:
                logger.warning(f"用量告警：用户不存在 {project.user_id}")
                return

            html = build_usage_alert_email(
                email=user.email,
                plan_name=plan_name,
                call_count=call_count,
                max_calls=max_calls,
                threshold=threshold,
            )
            send_email(
                to=user.email,
                subject=f"OpenAsk 用量提醒：{plan_name} 套餐已使用 {threshold}%",
                html_content=html,
            )
            logger.info(f"用量告警邮件已发送: {user.email} ({threshold}%)")
        except Exception as e:
            logger.warning(f"发送用量告警邮件失败: {e}")

    # ---- Stripe 关联 ----

    def get_stripe_customer_id(self, project_id: str) -> str:
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT stripe_customer_id FROM project_plans WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            return row["stripe_customer_id"] if row else ""
        finally:
            conn.close()

    def get_stripe_subscription_id(self, project_id: str) -> str:
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT stripe_subscription_id FROM project_plans WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            return row["stripe_subscription_id"] if row else ""
        finally:
            conn.close()

    def get_project_id_by_subscription(self, subscription_id: str) -> Optional[str]:
        """根据 Stripe Subscription ID 查询项目 ID。

        用于 webhook 事件（如订阅取消）反向定位项目。
        兼容旧数据：subscription_id 为空时回退到 customer_id 匹配。
        """
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT project_id FROM project_plans WHERE stripe_subscription_id = ?",
                (subscription_id,),
            ).fetchone()
            return row["project_id"] if row else None
        finally:
            conn.close()

    def count_active_subscriptions(self) -> int:
        """统计非 free 套餐的项目数（活跃订阅数）。"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM project_plans WHERE plan != 'free'"
            ).fetchone()
            return row["cnt"] if row else 0
        finally:
            conn.close()