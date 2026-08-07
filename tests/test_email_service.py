"""邮件服务测试 — HTML 构建、console/resend 发送、异常处理。"""

import logging
from unittest.mock import Mock, patch

import pytest

from src.services.email_service import (
    send_email,
    build_verification_email,
    build_password_reset_email,
    build_usage_alert_email,
    build_handoff_email,
)


# ================================================================
# HTML 构建
# ================================================================


class TestBuildEmailTemplates:
    def test_verification_email(self):
        html = build_verification_email("user@example.com", "tok123", "https://app.openask.dev")
        assert "user@example.com" in html
        assert "tok123" in html
        assert "verify-email" in html
        assert "24 小时" in html

    def test_verification_email_has_escapeable_fields(self):
        html = build_verification_email("u@e.com", "token", "https://x.com")
        assert "https://x.com/verify-email?token=token" in html

    def test_password_reset_email(self):
        html = build_password_reset_email("user@example.com", "reset_tok", "https://app.openask.dev")
        assert "reset_tok" in html
        assert "reset-password" in html
        assert "15 分钟" in html
        assert "设置新密码" in html

    def test_usage_alert_email_over_100(self):
        html = build_usage_alert_email("user@example.com", "Free", 1000, 1000, 100)
        assert "本月用量已用完" in html
        assert "1,000 / 1,000" in html
        assert "剩余额度" in html
        assert "0 次" in html

    def test_usage_alert_email_90(self):
        html = build_usage_alert_email("user@example.com", "Pro", 900, 1000, 90)
        assert "用量接近上限" in html
        assert "剩余额度" in html
        assert "100 次" in html

    def test_usage_alert_email_80(self):
        html = build_usage_alert_email("user@example.com", "Pro", 800, 1000, 80)
        assert "用量提醒" in html
        assert "剩余额度" in html

    def test_usage_alert_email_zero_max(self):
        html = build_usage_alert_email("user@example.com", "Free", 0, 0, 80)
        assert "用量提醒" in html

    def test_handoff_email_full(self):
        html = build_handoff_email(
            "owner@example.com",
            "我的店铺",
            "退货流程是什么？",
            contact_email="customer@example.com",
            contact_phone="+8613800000000",
            note="用户很着急",
        )
        assert "我的店铺" in html
        assert "退货流程是什么？" in html
        assert "customer@example.com" in html
        assert "+8613800000000" in html
        assert "用户很着急" in html

    def test_handoff_email_minimal(self):
        html = build_handoff_email("owner@example.com", "项目A", "问题")
        assert "项目A" in html
        assert "联系邮箱" not in html
        assert "联系电话" not in html
        assert "补充说明" not in html


# ================================================================
# 发送（console 模式）
# ================================================================


class TestSendEmailConsole:
    @patch("src.services.email_service.settings")
    def test_console_provider(self, mock_settings, caplog):
        mock_settings.email.provider = "console"
        with caplog.at_level(logging.INFO):
            result = send_email("a@b.com", "主题", "<p>你好</p>")
        assert result is True
        assert "邮件发送 [开发模式]" in caplog.text
        assert "a@b.com" in caplog.text

    @patch("src.services.email_service.settings")
    def test_empty_provider_falls_back_to_console(self, mock_settings, caplog):
        mock_settings.email.provider = ""
        with caplog.at_level(logging.INFO):
            result = send_email("a@b.com", "主题", "<b>内容</b>")
        assert result is True
        assert "邮件发送 [开发模式]" in caplog.text

    @patch("src.services.email_service.settings")
    def test_unknown_provider_falls_back_to_console(self, mock_settings, caplog):
        mock_settings.email.provider = "unknown_provider"
        with caplog.at_level(logging.WARNING):
            result = send_email("a@b.com", "主题", "<i>内容</i>")
        assert result is True
        assert "未知邮件提供商" in caplog.text


# ================================================================
# 发送（resend 模式）
# ================================================================


class TestSendEmailResend:
    @patch("src.services.email_service.settings")
    @patch("requests.post")
    def test_resend_success(self, mock_post, mock_settings):
        mock_settings.email.provider = "resend"
        mock_settings.email.resend_api_key = "re_123"
        mock_settings.email.from_addr = "noreply@openask.dev"

        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        mock_post.return_value = mock_resp

        result = send_email("a@b.com", "主题", "<p>hi</p>", text_content="hi")

        assert result is True
        mock_post.assert_called_once()
        # 校验请求体
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer re_123"
        assert call_kwargs["json"]["from"] == "noreply@openask.dev"
        assert call_kwargs["json"]["to"] == ["a@b.com"]
        assert call_kwargs["json"]["text"] == "hi"

    @patch("src.services.email_service.settings")
    @patch("requests.post")
    def test_resend_failure_status(self, mock_post, mock_settings):
        mock_settings.email.provider = "resend"
        mock_settings.email.resend_api_key = "re_123"
        mock_settings.email.from_addr = "noreply@openask.dev"

        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        mock_post.return_value = mock_resp

        result = send_email("a@b.com", "主题", "<p>hi</p>")

        assert result is False

    @patch("src.services.email_service.settings")
    @patch("requests.post")
    def test_resend_exception(self, mock_post, mock_settings):
        mock_settings.email.provider = "resend"
        mock_settings.email.resend_api_key = "re_123"
        mock_settings.email.from_addr = "noreply@openask.dev"

        mock_post.side_effect = Exception("network error")

        result = send_email("a@b.com", "主题", "<p>hi</p>")

        assert result is False