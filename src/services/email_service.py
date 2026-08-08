"""邮件发送服务。

开发环境：输出到日志（控制台）
生产环境：使用 Resend API 发送
"""

import logging
from typing import Optional

from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


def send_email(
    to: str,
    subject: str,
    html_content: str,
    text_content: Optional[str] = None,
) -> bool:
    """发送邮件。

    Args:
        to: 收件人邮箱
        subject: 邮件主题
        html_content: HTML 正文
        text_content: 纯文本正文（可选）

    Returns:
        是否发送成功
    """
    provider = settings.email.provider

    if provider == 'console' or not provider:
        _send_console(to, subject, html_content)
        return True

    if provider == 'resend':
        return _send_resend(to, subject, html_content, text_content)

    logger.warning(f"未知邮件提供商: {provider}，使用控制台输出")
    _send_console(to, subject, html_content)
    return True


def _send_console(to: str, subject: str, html_content: str) -> None:
    """开发环境：输出到控制台。"""
    logger.info("=" * 60)
    logger.info(f"📧 邮件发送 [开发模式]")
    logger.info(f"   收件人: {to}")
    logger.info(f"   主题: {subject}")
    logger.info(f"   内容:")
    # 提取纯文本部分
    import re
    text = re.sub(r'<[^>]+>', '', html_content)
    for line in text.strip().split('\n'):
        logger.info(f"     {line.strip()}")
    logger.info("=" * 60)


def _send_resend(
    to: str,
    subject: str,
    html_content: str,
    text_content: Optional[str] = None,
) -> bool:
    """生产环境：通过 Resend API 发送。"""
    try:
        import requests
        api_key = settings.email.resend_api_key
        from_email = settings.email.from_addr

        resp = requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'from': from_email,
                'to': [to],
                'subject': subject,
                'html': html_content,
                'text': text_content or '',
            },
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info(f"邮件发送成功: {to} ({subject})")
            return True
        else:
            logger.error(f"邮件发送失败: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logger.error(f"邮件发送异常: {e}")
        return False


def build_verification_email(email: str, token: str, base_url: str) -> str:
    """构建邮箱验证邮件 HTML。"""
    verify_url = f"{base_url}/verify-email?token={token}"
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 40px; background: #f5f7fa;">
    <div style="max-width: 480px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 32px; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
        <div style="text-align: center; margin-bottom: 24px;">
            <div style="width: 48px; height: 48px; margin: 0 auto 12px; border-radius: 10px; background: linear-gradient(135deg, #409eff, #337ecc); color: #fff; font-size: 22px; font-weight: 700; display: flex; align-items: center; justify-content: center;">O</div>
            <h1 style="font-size: 20px; font-weight: 600; margin: 0; color: #303133;">验证邮箱地址</h1>
        </div>
        <p style="font-size: 14px; color: #606266; line-height: 1.6;">你好！</p>
        <p style="font-size: 14px; color: #606266; line-height: 1.6;">请点击下方按钮验证你的邮箱 <strong>{email}</strong>：</p>
        <div style="text-align: center; margin: 28px 0;">
            <a href="{verify_url}" style="display: inline-block; padding: 12px 32px; background: linear-gradient(135deg, #409eff, #337ecc); color: #fff; text-decoration: none; border-radius: 6px; font-size: 15px; font-weight: 500;">验证邮箱</a>
        </div>
        <p style="font-size: 13px; color: #909399; line-height: 1.6;">如果按钮无法点击，请复制以下链接到浏览器：</p>
        <p style="font-size: 12px; color: #409eff; word-break: break-all;">{verify_url}</p>
        <p style="font-size: 13px; color: #909399; margin-top: 24px;">此链接 24 小时内有效。如果你没有注册 OpenAsk 账号，请忽略此邮件。</p>
        <hr style="border: none; border-top: 1px solid #e4e7ed; margin: 24px 0;">
        <p style="font-size: 12px; color: #c0c4cc; text-align: center;">OpenAsk — AI 智能客服知识库</p>
    </div>
</body>
</html>"""


def build_password_reset_email(email: str, token: str, base_url: str) -> str:
    """构建密码重置邮件 HTML。"""
    reset_url = f"{base_url}/reset-password?token={token}"
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 40px; background: #f5f7fa;">
    <div style="max-width: 480px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 32px; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
        <div style="text-align: center; margin-bottom: 24px;">
            <div style="width: 48px; height: 48px; margin: 0 auto 12px; border-radius: 10px; background: linear-gradient(135deg, #409eff, #337ecc); color: #fff; font-size: 22px; font-weight: 700; display: flex; align-items: center; justify-content: center;">O</div>
            <h1 style="font-size: 20px; font-weight: 600; margin: 0; color: #303133;">重置密码</h1>
        </div>
        <p style="font-size: 14px; color: #606266; line-height: 1.6;">你好！</p>
        <p style="font-size: 14px; color: #606266; line-height: 1.6;">我们收到了你的密码重置请求，请点击下方按钮设置新密码：</p>
        <div style="text-align: center; margin: 28px 0;">
            <a href="{reset_url}" style="display: inline-block; padding: 12px 32px; background: linear-gradient(135deg, #409eff, #337ecc); color: #fff; text-decoration: none; border-radius: 6px; font-size: 15px; font-weight: 500;">重置密码</a>
        </div>
        <p style="font-size: 13px; color: #909399; line-height: 1.6;">如果按钮无法点击，请复制以下链接到浏览器：</p>
        <p style="font-size: 12px; color: #409eff; word-break: break-all;">{reset_url}</p>
        <p style="font-size: 13px; color: #909399; margin-top: 24px;">此链接 15 分钟内有效。如果你没有请求重置密码，请忽略此邮件。</p>
        <hr style="border: none; border-top: 1px solid #e4e7ed; margin: 24px 0;">
        <p style="font-size: 12px; color: #c0c4cc; text-align: center;">OpenAsk — AI 智能客服知识库</p>
    </div>
</body>
</html>"""


def build_usage_alert_email(
    email: str,
    plan_name: str,
    call_count: int,
    max_calls: int,
    threshold: int,
) -> str:
    """构建用量告警邮件 HTML。

    Args:
        email: 收件人邮箱
        plan_name: 套餐名称（Free / Pro / Enterprise）
        call_count: 当月已用调用次数
        max_calls: 套餐月度上限
        threshold: 告警阈值百分比（80 / 90 / 100）
    """
    percent = int(call_count / max_calls * 100) if max_calls else 0
    remaining = max(0, max_calls - call_count)

    if threshold >= 100:
        title = "本月用量已用完"
        desc = "你的月度 API 调用额度已用完，新的请求将被限制。升级套餐或等待下月重置。"
        alert_color = "#f56c6c"
    elif threshold >= 90:
        title = "用量接近上限"
        desc = "你的月度 API 调用额度即将用完，请留意剩余额度。"
        alert_color = "#e6a23c"
    else:
        title = "用量提醒"
        desc = "你的月度 API 调用额度已使用较多，建议关注用量情况。"
        alert_color = "#409eff"

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 40px; background: #f5f7fa;">
    <div style="max-width: 480px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 32px; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
        <div style="text-align: center; margin-bottom: 24px;">
            <div style="width: 48px; height: 48px; margin: 0 auto 12px; border-radius: 10px; background: linear-gradient(135deg, #409eff, #337ecc); color: #fff; font-size: 22px; font-weight: 700; display: flex; align-items: center; justify-content: center;">O</div>
            <h1 style="font-size: 20px; font-weight: 600; margin: 0; color: #303133;">{title}</h1>
        </div>
        <p style="font-size: 14px; color: #606266; line-height: 1.6;">你好！</p>
        <p style="font-size: 14px; color: #606266; line-height: 1.6;">{desc}</p>
        <div style="background: #f5f7fa; border-radius: 8px; padding: 16px 20px; margin: 20px 0;">
            <div style="display: flex; justify-content: space-between; font-size: 13px; color: #606266; margin-bottom: 8px;">
                <span>当前套餐</span><strong style="color: #303133;">{plan_name}</strong>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 13px; color: #606266; margin-bottom: 8px;">
                <span>本月已用</span><strong style="color: {alert_color};">{call_count:,} / {max_calls:,} 次</strong>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 13px; color: #606266;">
                <span>剩余额度</span><strong style="color: #303133;">{remaining:,} 次（{percent}%）</strong>
            </div>
            <div style="height: 6px; background: #e4e7ed; border-radius: 3px; margin-top: 12px; overflow: hidden;">
                <div style="height: 100%; width: {min(percent, 100)}%; background: {alert_color}; border-radius: 3px;"></div>
            </div>
        </div>
        <div style="text-align: center; margin: 24px 0;">
            <a href="https://openask.dev" style="display: inline-block; padding: 12px 32px; background: linear-gradient(135deg, #409eff, #337ecc); color: #fff; text-decoration: none; border-radius: 6px; font-size: 15px; font-weight: 500;">查看用量</a>
        </div>
        <p style="font-size: 13px; color: #909399; line-height: 1.6;">此邮件由 OpenAsk 自动发送至 {email}。如需调整套餐，请前往控制台的「项目设置」页面。</p>
        <hr style="border: none; border-top: 1px solid #e4e7ed; margin: 24px 0;">
        <p style="font-size: 12px; color: #c0c4cc; text-align: center;">OpenAsk — AI 智能客服知识库</p>
    </div>
</body>
</html>"""


def build_handoff_email(
    email: str,
    project_name: str,
    query: str,
    contact_email: str = "",
    contact_phone: str = "",
    note: str = "",
) -> str:
    """构建人工客服转接通知邮件 HTML。"""
    contact_info = ""
    if contact_email:
        contact_info += f"<p style='font-size:14px;color:#606266;line-height:1.6;'><strong>联系邮箱：</strong>{contact_email}</p>"
    if contact_phone:
        contact_info += f"<p style='font-size:14px;color:#606266;line-height:1.6;'><strong>联系电话：</strong>{contact_phone}</p>"
    note_html = f"<p style='font-size:14px;color:#606266;line-height:1.6;'><strong>补充说明：</strong>{note}</p>" if note else ""

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 40px; background: #f5f7fa;">
    <div style="max-width: 480px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 32px; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
        <div style="text-align: center; margin-bottom: 24px;">
            <div style="width: 48px; height: 48px; margin: 0 auto 12px; border-radius: 10px; background: linear-gradient(135deg, #f56c6c, #e6a23c); color: #fff; font-size: 22px; font-weight: 700; display: flex; align-items: center; justify-content: center;">!</div>
            <h1 style="font-size: 20px; font-weight: 600; margin: 0; color: #303133;">新的人工客服转接请求</h1>
        </div>
        <p style="font-size: 14px; color: #606266; line-height: 1.6;">你好！</p>
        <p style="font-size: 14px; color: #606266; line-height: 1.6;">项目 <strong>{project_name}</strong> 收到一个新的人工客服转接请求：</p>
        <div style="background: #f5f7fa; border-radius: 8px; padding: 16px 20px; margin: 20px 0;">
            <p style="font-size: 14px; color: #606266; line-height: 1.6; margin: 0 0 8px;"><strong>用户问题：</strong></p>
            <p style="font-size: 14px; color: #303133; line-height: 1.6; background: #fff; padding: 12px; border-radius: 6px; margin: 0;">{query}</p>
        </div>
        {contact_info}
        {note_html}
        <div style="text-align: center; margin: 24px 0;">
            <a href="https://openask.dev" style="display: inline-block; padding: 12px 32px; background: linear-gradient(135deg, #409eff, #337ecc); color: #fff; text-decoration: none; border-radius: 6px; font-size: 15px; font-weight: 500;">查看转接请求</a>
        </div>
        <p style="font-size: 13px; color: #909399; line-height: 1.6;">请尽快联系用户处理此请求。此邮件由 OpenAsk 自动发送。</p>
        <hr style="border: none; border-top: 1px solid #e4e7ed; margin: 24px 0;">
        <p style="font-size: 12px; color: #c0c4cc; text-align: center;">OpenAsk — AI 智能客服知识库</p>
    </div>
</body>
</html>"""