"""Billing API 测试 — 套餐、Stripe Checkout、Webhook、门户、账单。"""

from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.billing import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


_EMAIL_COUNTER = 0


def unique_email():
    global _EMAIL_COUNTER
    _EMAIL_COUNTER += 1
    return f"bill_{_EMAIL_COUNTER}@test.com"


# ================================================================
# 套餐
# ================================================================


class TestPlan:
    def test_get_plan_requires_auth(self, client):
        """未登录返回 401。"""
        resp = client.get("/api/billing/plan?project_id=proj_1")
        assert resp.status_code == 401

    def test_get_plan_authenticated(self, client):
        """已登录返回套餐信息。"""
        # 先注册获取 token
        from src.api.auth import router as auth_router
        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(router)
        auth_client = TestClient(app)

        email = unique_email()
        reg = auth_client.post("/api/auth/register", json={
            "email": email, "password": "password123", "name": "计费测试",
        }).json()
        token = reg["access_token"]
        project_id = reg["project"]["project_id"]

        resp = auth_client.get(f"/api/billing/plan?project_id={project_id}",
                               headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] == "free"
        assert "limits" in data
        assert "usage" in data


# ================================================================
# Stripe Checkout
# ================================================================


class TestCreateCheckout:
    def test_stripe_not_configured(self, client):
        """Stripe 未配置时返回 503。"""
        # 先注册
        from src.api.auth import router as auth_router
        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(router)
        auth_client = TestClient(app)

        email = unique_email()
        reg = auth_client.post("/api/auth/register", json={
            "email": email, "password": "password123",
        }).json()
        token = reg["access_token"]
        project_id = reg["project"]["project_id"]

        resp = auth_client.post("/api/billing/create-checkout",
                                json={"project_id": project_id, "plan": "pro"},
                                headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 503

    @patch("src.api.billing.settings")
    def test_stripe_success(self, mock_settings, client):
        """Stripe 配置正常时创建 checkout 成功。"""
        mock_settings.stripe.secret_key = "sk_test_xxx"
        mock_settings.stripe.price_pro = "price_pro"
        mock_settings.stripe.price_enterprise = "price_ent"
        mock_settings.api.frontend_url = "http://localhost:5173"

        from src.api.auth import router as auth_router
        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(router)
        auth_client = TestClient(app)

        email = unique_email()
        reg = auth_client.post("/api/auth/register", json={
            "email": email, "password": "password123",
        }).json()
        token = reg["access_token"]
        project_id = reg["project"]["project_id"]

        # stripe 未安装，mock 整个模块
        mock_stripe = Mock()
        mock_customer = Mock()
        mock_customer.create.return_value = Mock(id="cus_123")
        mock_stripe.Customer = mock_customer
        mock_checkout = Mock()
        mock_checkout.Session.create.return_value = Mock(url="https://checkout.stripe.com/session")
        mock_stripe.checkout = mock_checkout

        with patch.dict("sys.modules", {"stripe": mock_stripe}):
            resp = auth_client.post("/api/billing/create-checkout",
                                    json={"project_id": project_id, "plan": "pro"},
                                    headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            data = resp.json()
            assert "url" in data
            assert data["url"] == "https://checkout.stripe.com/session"


# ================================================================
# Webhook
# ================================================================


class TestWebhook:
    @patch("src.api.billing.settings")
    def test_webhook_no_secret(self, mock_settings, client):
        mock_settings.stripe.webhook_secret = ""
        resp = client.post("/api/billing/stripe/webhook", data=b"{}", headers={
            "stripe-signature": "test",
            "Content-Type": "application/json",
        })
        assert resp.status_code == 503

    @patch("src.api.billing.settings")
    def test_webhook_invalid_signature(self, mock_settings, client):
        mock_settings.stripe.webhook_secret = "whsec_test"
        mock_stripe = Mock()
        mock_stripe.Webhook.construct_event.side_effect = Exception("SignatureVerificationError")
        mock_stripe.error = Mock()
        mock_stripe.error.SignatureVerificationError = type("SignatureVerificationError", (Exception,), {})
        mock_stripe.Webhook.construct_event.side_effect = mock_stripe.error.SignatureVerificationError("invalid", "sig")

        with patch.dict("sys.modules", {"stripe": mock_stripe}):
            resp = client.post("/api/billing/stripe/webhook", data=b"{}", headers={
                "stripe-signature": "bad",
                "Content-Type": "application/json",
            })
            assert resp.status_code == 400


# ================================================================
# 门户
# ================================================================


class TestPortal:
    def test_portal_requires_auth(self, client):
        resp = client.post("/api/billing/portal")
        assert resp.status_code == 401


# ================================================================
# 账单
# ================================================================


class TestInvoices:
    def test_invoices_requires_auth(self, client):
        resp = client.get("/api/billing/invoices")
        assert resp.status_code == 401