"""套餐服务测试 — 套餐管理、用量记录、限制检查、用量告警、Stripe 关联。"""

import os
import tempfile
from unittest.mock import Mock, patch

import pytest

from src.services.plan_service import PlanService, PLAN_LIMITS


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def service(db_path):
    return PlanService(db_path=db_path)


# ================================================================
# 套餐管理
# ================================================================


class TestPlanManagement:
    def test_default_plan_is_free(self, service):
        """新项目默认 Free 套餐。"""
        assert service.get_plan("proj_new") == "free"

    def test_set_plan(self, service):
        """设置套餐后可以读取。"""
        service.set_plan("proj_1", "pro")
        assert service.get_plan("proj_1") == "pro"

    def test_set_plan_upgrade(self, service):
        """升级套餐。"""
        service.set_plan("proj_1", "free")
        service.set_plan("proj_1", "enterprise")
        assert service.get_plan("proj_1") == "enterprise"

    def test_set_plan_with_stripe_ids(self, service):
        """设置套餐时附上 Stripe ID。"""
        service.set_plan(
            "proj_1", "pro",
            stripe_subscription_id="sub_123",
            stripe_customer_id="cus_456",
        )
        assert service.get_plan("proj_1") == "pro"
        assert service.get_stripe_customer_id("proj_1") == "cus_456"
        assert service.get_stripe_subscription_id("proj_1") == "sub_123"

    def test_get_plan_limits_free(self, service):
        """Free 套餐限制正确。"""
        limits = service.get_plan_limits("proj_free")
        assert limits["max_documents"] == 100
        assert limits["max_monthly_calls"] == 1000
        assert limits["max_projects"] == 3
        assert limits["price_monthly"] == 0

    def test_get_plan_limits_pro(self, service):
        """Pro 套餐限制正确。"""
        service.set_plan("proj_pro", "pro")
        limits = service.get_plan_limits("proj_pro")
        assert limits["max_documents"] == 1000
        assert limits["max_monthly_calls"] == 10000
        assert limits["price_monthly"] == 2900

    def test_get_plan_limits_enterprise(self, service):
        """Enterprise 套餐限制正确。"""
        service.set_plan("proj_ent", "enterprise")
        limits = service.get_plan_limits("proj_ent")
        assert limits["max_documents"] == 10000
        assert limits["max_projects"] == 999

    def test_unknown_plan_falls_back_to_free(self, service):
        """未知套餐回退到 Free。"""
        service.set_plan("proj_bad", "nonexistent_plan")
        limits = service.get_plan_limits("proj_bad")
        assert limits["name"] == "Free"


# ================================================================
# 用量记录
# ================================================================


class TestUsageRecording:
    def test_usage_default_zero(self, service):
        """未使用过的项目用量为 0。"""
        usage = service.get_usage("proj_1")
        assert usage["call_count"] == 0
        assert usage["prompt_tokens"] == 0
        assert usage["completion_tokens"] == 0

    def test_record_usage(self, service):
        """记录用量后可以查询。"""
        service.record_usage("proj_1", call_count=1, prompt_tokens=100, completion_tokens=50)
        usage = service.get_usage("proj_1")
        assert usage["call_count"] == 1
        assert usage["prompt_tokens"] == 100
        assert usage["completion_tokens"] == 50

    def test_record_usage_accumulates(self, service):
        """多次记录累加。"""
        service.record_usage("proj_1", call_count=1, prompt_tokens=50, completion_tokens=25)
        service.record_usage("proj_1", call_count=1, prompt_tokens=30, completion_tokens=15)
        usage = service.get_usage("proj_1")
        assert usage["call_count"] == 2
        assert usage["prompt_tokens"] == 80
        assert usage["completion_tokens"] == 40

    def test_usage_isolated_by_project(self, service):
        """不同项目用量独立。"""
        service.record_usage("proj_a", call_count=5)
        service.record_usage("proj_b", call_count=3)
        assert service.get_usage("proj_a")["call_count"] == 5
        assert service.get_usage("proj_b")["call_count"] == 3

    def test_record_usage_updates_current_month(self, service):
        """记录在当前月份下。"""
        from datetime import datetime, timezone
        ym = datetime.now(timezone.utc).strftime("%Y-%m")
        service.record_usage("proj_1", call_count=1)
        usage = service.get_usage("proj_1")
        assert usage["call_count"] == 1


# ================================================================
# 限制检查
# ================================================================


class TestCheckLimits:
    def test_check_limits_allowed(self, service):
        """未超限时返回 allowed=True。"""
        result = service.check_limits("proj_1", document_count=1, project_count=1)
        assert result["allowed"] is True

    def test_check_limits_document_exceeded(self, service):
        """文档数超限返回 allowed=False。"""
        result = service.check_limits("proj_1", document_count=9999, project_count=1)
        assert result["allowed"] is False
        assert "文档数" in result["reason"]

    def test_check_limits_projects_exceeded(self, service):
        """项目数超限返回 allowed=False。"""
        result = service.check_limits("proj_1", document_count=1, project_count=999)
        assert result["allowed"] is False
        assert "项目数" in result["reason"]

    def test_check_limits_calls_exceeded(self, service):
        """调用次数超限返回 allowed=False。"""
        service.record_usage("proj_1", call_count=9999)
        result = service.check_limits("proj_1", document_count=1, project_count=1)
        assert result["allowed"] is False
        assert "调用次数" in result["reason"]

    def test_check_limits_returns_limits_and_usage(self, service):
        """检查结果包含 limits 和 usage 信息。"""
        result = service.check_limits("proj_1", document_count=1, project_count=1)
        assert "limits" in result
        assert "usage" in result
        assert result["limits"]["name"] == "Free"


# ================================================================
# 用量告警
# ================================================================


class TestUsageAlerts:
    def test_alert_not_sent_below_threshold(self, service):
        """低于阈值不发送告警。"""
        service.record_usage("proj_1", call_count=1)
        # 阈值 80%，1000 次只用 1 次，不应触发
        assert service._alert_was_sent("proj_1", service.get_current_month(), 80) is False

    def test_alert_sent_at_threshold(self, service):
        """达到阈值后记录已发送。"""
        ym = service.get_current_month()
        service._mark_alert_sent("proj_1", ym, 80)
        assert service._alert_was_sent("proj_1", ym, 80) is True

    def test_alert_was_sent_returns_false_for_unsent(self, service):
        """未发送的阈值返回 False。"""
        assert service._alert_was_sent("proj_1", "2099-01", 80) is False

    def test_alert_thresholds_independent(self, service):
        """不同阈值独立记录。"""
        ym = service.get_current_month()
        service._mark_alert_sent("proj_1", ym, 80)
        assert service._alert_was_sent("proj_1", ym, 80) is True
        assert service._alert_was_sent("proj_1", ym, 90) is False


# ================================================================
# Stripe 关联
# ================================================================


class TestStripeAssociations:
    def test_stripe_ids_default_empty(self, service):
        """未设置时 Stripe ID 为空字符串。"""
        assert service.get_stripe_customer_id("proj_1") == ""
        assert service.get_stripe_subscription_id("proj_1") == ""

    def test_set_and_get_stripe_ids(self, service):
        """设置后可以正确获取。"""
        service.set_plan(
            "proj_1", "pro",
            stripe_subscription_id="sub_abc",
            stripe_customer_id="cus_xyz",
        )
        assert service.get_stripe_customer_id("proj_1") == "cus_xyz"
        assert service.get_stripe_subscription_id("proj_1") == "sub_abc"

    def test_get_project_id_by_subscription(self, service):
        """通过 subscription_id 反查 project_id。"""
        service.set_plan("proj_1", "pro", stripe_subscription_id="sub_1")
        assert service.get_project_id_by_subscription("sub_1") == "proj_1"

    def test_get_project_id_by_subscription_not_found(self, service):
        """不存在的 subscription_id 返回 None。"""
        assert service.get_project_id_by_subscription("sub_404") is None


# ================================================================
# PLAN_LIMITS 数据完整性
# ================================================================


class TestPlanLimitsData:
    def test_all_plans_have_required_keys(self):
        required = {"name", "price_monthly", "max_documents", "max_monthly_calls",
                     "max_projects", "rate_per_minute", "custom_llm", "team_members", "support"}
        for plan, limits in PLAN_LIMITS.items():
            for key in required:
                assert key in limits, f"{plan} 缺少 {key}"

    def test_plans_have_ascending_prices(self):
        """套餐价格递增。"""
        prices = [PLAN_LIMITS[p]["price_monthly"] for p in ["free", "pro", "enterprise"]]
        assert prices == sorted(prices)

    def test_free_plan_is_zero(self):
        assert PLAN_LIMITS["free"]["price_monthly"] == 0