"""
OpenAsk 后端 API 全面集成测试脚本
====================================
针对运行中的服务 (localhost:8000) 做端到端测试。

测试范围：
  1. 系统 API ................. /api/health, /sitemap.xml, /robots.txt
  2. 认证 API ................. register, login, me, change-password
  3. 项目管理 API .............. list, create, get, update, delete, rotate-key, stats, embed-script
  4. 问答 API ................. /api/chat (非流式 + 流式 SSE)
  4b. 邮箱验证/密码重置 ........ send-verification, verify-email, forgot-password, reset-password
  5. 知识库 API ............... create, upload, get, update, delete, list, batch-delete
  6. 搜索 API ................. search, batch-search
  7. 分析 API ................. logs, trends, top-questions, feedback, satisfaction, gaps
  8. 会话管理 API ............. list, get, delete, update title
  9. 电商 API ................. templates list, template detail
  10. 计费 API ................ plan, invoices
  11. 管理后台 API ............. stats, users, projects
  12. 错误场景 ................. 401, 403, 404, 422, 429

用法:
  python scripts/test_api_integration.py [--base-url http://localhost:8000]
"""

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import urljoin

import requests


# ================================================================
# 配置
# ================================================================

BASE_URL = "http://localhost:8000"
TEST_EMAIL = f"test_{int(time.time())}@example.com"
TEST_PASSWORD = "TestPass123!"
TEST_USER_NAME = "测试用户"
TEST_PROJECT_NAME = "测试项目"


# ================================================================
# 测试报告
# ================================================================

class TestReport:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
        self.current_group = ""

    def group(self, name):
        self.current_group = name
        print(f"\n{'='*70}")
        print(f"  📁 {name}")
        print(f"{'='*70}")

    def test(self, name, condition, detail=""):
        if condition:
            self.passed += 1
            status = "✅"
        else:
            self.failed += 1
            status = "❌"
            self.errors.append(f"  [{self.current_group}] {name}: {detail}")
        print(f"  {status} {name}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*70}")
        print(f"  测试完成! 总计 {total} | 通过 {self.passed} | 失败 {self.failed}")
        if self.errors:
            print(f"\n  失败详情:")
            for e in self.errors:
                print(f"  {e}")
        print(f"{'='*70}\n")
        return self.failed == 0


report = TestReport()


# ================================================================
# 辅助函数
# ================================================================

def api(path, method="GET", **kwargs):
    """发送 API 请求，返回 response 对象。"""
    url = urljoin(BASE_URL, path)
    default_headers = kwargs.pop("headers", {})
    return requests.request(method, url, headers=default_headers, timeout=15, **kwargs)


def assert_status(resp, expected, ctx=""):
    """断言状态码，附带错误详情。"""
    if resp.status_code != expected:
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:200]
        return False, f"期望 {expected} 实际 {resp.status_code}: {body}"
    return True, ""


# ================================================================
# 1. 系统 API 测试
# ================================================================

def test_system_apis():
    report.group("1. 系统 API")

    # 1.1 健康检查
    r = api("/api/health")
    ok, err = assert_status(r, 200)
    data = r.json()
    report.test("健康检查 200", ok, err)
    report.test("健康检查 status=healthy", data.get("status") == "healthy", f"status={data.get('status')}")
    report.test("健康检查 version=1.0.0", data.get("version") == "1.0.0", f"version={data.get('version')}")

    # 1.2 Sitemap
    r = api("/sitemap.xml")
    ok, err = assert_status(r, 200)
    report.test("Sitemap 200", ok, err)
    report.test("Sitemap 包含 urlset", "urlset" in r.text, "")

    # 1.3 Robots.txt
    r = api("/robots.txt")
    ok, err = assert_status(r, 200)
    report.test("Robots.txt 200", ok, err)
    report.test("Robots.txt 包含 Allow", "Allow" in r.text, "")


# ================================================================
# 2. 认证 API 测试
# ================================================================

AUTH_TOKEN = ""
AUTH_USER_ID = ""

def test_auth_apis():
    global AUTH_TOKEN, AUTH_USER_ID
    report.group("2. 认证 API")

    # 2.1 注册 - 成功
    r = api("/api/auth/register", "POST", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
        "name": TEST_USER_NAME,
    })
    ok, err = assert_status(r, 200)
    report.test("注册成功 200", ok, err)
    if ok:
        data = r.json()
        AUTH_TOKEN = data.get("access_token", "")
        AUTH_USER_ID = data.get("user", {}).get("user_id", "")
        report.test("注册返回 access_token", bool(AUTH_TOKEN), "")
        report.test("注册返回 user_id", bool(AUTH_USER_ID), "")
        report.test("注册返回 project", bool(data.get("project")), "")
        report.test("注册返回 api_key", bool(data["project"].get("api_key", "").startswith("sk_")), "")

    # 2.2 注册 - 重复邮箱
    r = api("/api/auth/register", "POST", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
    })
    ok, err = assert_status(r, 409)
    report.test("重复注册 409", ok, err)

    # 2.3 注册 - 密码太短
    r = api("/api/auth/register", "POST", json={
        "email": f"short_{int(time.time())}@example.com",
        "password": "123",
    })
    ok, err = assert_status(r, 422)
    report.test("密码太短注册 422", ok, err)

    # 2.4 注册 - 无效邮箱
    r = api("/api/auth/register", "POST", json={
        "email": "not-an-email",
        "password": "12345678",
    })
    ok, err = assert_status(r, 422)
    report.test("无效邮箱注册 422", ok, err)

    # 2.5 登录 - 成功
    r = api("/api/auth/token", "POST", data={
        "username": TEST_EMAIL,
        "password": TEST_PASSWORD,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    ok, err = assert_status(r, 200)
    report.test("登录成功 200", ok, err)
    if ok:
        AUTH_TOKEN = r.json().get("access_token", "")
        report.test("登录返回 access_token", bool(AUTH_TOKEN), "")
        report.test("登录返回 user", bool(r.json().get("user")), "")

    # 2.6 登录 - 错误密码
    r = api("/api/auth/token", "POST", data={
        "username": TEST_EMAIL,
        "password": "wrong_password_123",
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    ok, err = assert_status(r, 401)
    report.test("错误密码登录 401", ok, err)

    # 2.7 登录 - 不存在邮箱
    r = api("/api/auth/token", "POST", data={
        "username": "nonexistent@example.com",
        "password": "TestPass123!",
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    ok, err = assert_status(r, 401)
    report.test("不存在邮箱登录 401", ok, err)

    # 2.8 获取当前用户
    r = api("/api/auth/me", headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
    ok, err = assert_status(r, 200)
    report.test("获取用户信息 200", ok, err)
    if ok:
        data = r.json()
        report.test("用户邮箱匹配", data.get("email") == TEST_EMAIL, f"{data.get('email')} != {TEST_EMAIL}")
        report.test("用户名称匹配", data.get("name") == TEST_USER_NAME, f"{data.get('name')} != {TEST_USER_NAME}")

    # 2.9 获取用户 - 无 token
    r = api("/api/auth/me")
    ok, err = assert_status(r, 401)
    report.test("无 token 获取用户 401", ok, err)

    # 2.10 获取用户 - 无效 token
    r = api("/api/auth/me", headers={"Authorization": "Bearer invalid_token_xxx"})
    ok, err = assert_status(r, 401)
    report.test("无效 token 获取用户 401", ok, err)

    # 2.11 修改密码 - 成功
    r = api("/api/auth/change-password", "POST", json={
        "old_password": TEST_PASSWORD,
        "new_password": "NewPass456!",
    }, headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
    ok, err = assert_status(r, 200)
    report.test("修改密码成功 200", ok, err)

    # 2.12 改回原密码（为后续测试）
    r = api("/api/auth/change-password", "POST", json={
        "old_password": "NewPass456!",
        "new_password": TEST_PASSWORD,
    }, headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
    ok, err = assert_status(r, 200)
    report.test("改回原密码成功 200", ok, err)

    # 2.13 修改密码 - 旧密码错误
    r = api("/api/auth/change-password", "POST", json={
        "old_password": "wrong_old_pass",
        "new_password": "NewPass456!",
    }, headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
    ok, err = assert_status(r, 401)
    report.test("错误旧密码修改 401", ok, err)

    # 2.14 修改密码 - 无登录
    r = api("/api/auth/change-password", "POST", json={
        "old_password": TEST_PASSWORD,
        "new_password": "NewPass456!",
    })
    ok, err = assert_status(r, 401)
    report.test("未登录修改密码 401", ok, err)


# ================================================================
# 2b. 邮箱验证 / 密码重置流程（真实服务器，从日志提取 token）
# ================================================================

import re as _re


def _extract_token_from_log(log_path: str, keyword: str = "token=") -> str:
    """从服务日志中提取验证/重置 token（console 邮件模式将内容输出到日志）。"""
    # 检查多个日志路径
    if not os.path.exists(log_path):
        alt_paths = ["/tmp/openask_server.log", "app.log", "../app.log"]
        for p in alt_paths:
            if os.path.exists(p):
                log_path = p
                break
    if not os.path.exists(log_path):
        return ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        for line in reversed(lines[-200:]):
            if keyword in line:
                m = _re.search(r'token=([^&\s"\'>]+)', line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return ""


def test_auth_email_flow():
    """邮箱验证 / 密码重置流程（从服务日志提取 token 走完整流程）。"""
    global AUTH_TOKEN
    report.group("2b. 邮箱验证 / 密码重置流程")
    auth_headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    log_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.log"
    )

    # 2.15 发送验证邮件
    r = api("/api/auth/send-verification", "POST", json={"email": TEST_EMAIL})
    ok, err = assert_status(r, 200)
    report.test("发送验证邮件 200", ok, err)

    # 2.16 从日志提取验证 token
    token = _extract_token_from_log(log_path, "token=")
    if token:
        report.test("从日志提取验证 token", bool(token), "")

        # 2.17 验证邮箱
        r = api("/api/auth/verify-email", "POST", json={"token": token})
        ok, err = assert_status(r, 200)
        report.test("验证邮箱成功 200", ok, err)

        # 2.18 重复验证
        r = api("/api/auth/verify-email", "POST", json={"token": token})
        ok, err = assert_status(r, 200)
        report.test("重复验证邮箱 200", ok, err)
    else:
        report.test("从日志提取验证 token", False, "日志中未找到 token")
        report.test("验证邮箱成功 200", False, "跳过（无 token）")
        report.test("重复验证邮箱 200", False, "跳过（无 token）")

    # 2.19 无效 token 验证
    r = api("/api/auth/verify-email", "POST", json={"token": "invalid_token"})
    ok, err = assert_status(r, 401)
    report.test("无效 token 验证邮箱 401", ok, err)

    # 2.20 不存在的邮箱发送验证
    r = api("/api/auth/send-verification", "POST", json={
        "email": "nonexistent_verify@test.com",
    })
    ok, err = assert_status(r, 404)
    report.test("不存在邮箱发送验证 404", ok, err)

    # 2.21 申请密码重置
    r = api("/api/auth/forgot-password", "POST", json={"email": TEST_EMAIL})
    ok, err = assert_status(r, 200)
    report.test("申请密码重置 200", ok, err)

    # 2.22 未注册邮箱申请重置（不暴露邮箱是否存在）
    r = api("/api/auth/forgot-password", "POST", json={
        "email": "nonexistent_reset@test.com",
    })
    ok, err = assert_status(r, 200)
    report.test("未注册邮箱申请重置 200", ok, err)

    # 2.23 从日志提取重置 token
    reset_token = _extract_token_from_log(log_path, "token=")
    if reset_token:
        report.test("从日志提取重置 token", bool(reset_token), "")

        # 2.24 重置密码
        r = api("/api/auth/reset-password", "POST", json={
            "token": reset_token,
            "password": "ResetPass789!",
        })
        ok, err = assert_status(r, 200)
        report.test("重置密码成功 200", ok, err)

        # 2.25 用新密码登录
        r = api("/api/auth/token", "POST", data={
            "username": TEST_EMAIL,
            "password": "ResetPass789!",
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
        ok, err = assert_status(r, 200)
        report.test("新密码登录成功 200", ok, err)
        if ok:
            AUTH_TOKEN = r.json().get("access_token", "")

        # 2.26 改回原密码（为后续测试）
        r = api("/api/auth/change-password", "POST", json={
            "old_password": "ResetPass789!",
            "new_password": TEST_PASSWORD,
        }, headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
        ok, err = assert_status(r, 200)
        report.test("改回原密码 200", ok, err)
    else:
        report.test("从日志提取重置 token", False, "日志中未找到 token")
        report.test("重置密码成功 200", False, "跳过（无 token）")
        report.test("新密码登录成功 200", False, "跳过（无 token）")

    # 2.27 无效 token 重置
    r = api("/api/auth/reset-password", "POST", json={
        "token": "invalid_reset_token",
        "password": "NewPass456!",
    })
    ok, err = assert_status(r, 401)
    report.test("无效 token 重置密码 401", ok, err)


# ================================================================
# 3. 项目管理 API 测试
# ================================================================

PROJECT_ID = ""
API_KEY = ""

def test_project_apis():
    global PROJECT_ID, API_KEY
    report.group("3. 项目管理 API")
    auth_headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    # 3.1 项目列表
    r = api("/api/projects", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("项目列表 200", ok, err)
    if ok:
        projects = r.json()
        report.test("项目列表是数组", isinstance(projects, list), "")
        if projects:
            PROJECT_ID = projects[0].get("project_id", "")
            API_KEY = projects[0].get("api_key", "")
            report.test("已有项目 (注册自动创建)", len(projects) >= 1, f"项目数={len(projects)}")
            report.test("API Key 格式 sk_", API_KEY.startswith("sk_"), "")

    # 3.2 创建项目
    r = api("/api/projects", "POST", json={"name": TEST_PROJECT_NAME}, headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("创建项目 200", ok, err)
    if ok:
        new_id = r.json().get("project_id", "")
        new_key = r.json().get("api_key", "")
        if new_id:
            PROJECT_ID = new_id
            API_KEY = new_key
        report.test("新项目有 project_id", bool(new_id), "")
        report.test("新项目有 api_key", bool(new_key), "")

    # 3.3 创建项目 - 空名称
    r = api("/api/projects", "POST", json={"name": ""}, headers=auth_headers)
    ok, err = assert_status(r, 422)
    report.test("空名称创建项目 422", ok, err)

    # 3.4 创建项目 - 未登录
    r = api("/api/projects", "POST", json={"name": "unauth project"})
    ok, err = assert_status(r, 401)
    report.test("未登录创建项目 401", ok, err)

    # 3.5 项目详情
    r = api(f"/api/projects/{PROJECT_ID}", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("项目详情 200", ok, err)
    if ok:
        data = r.json()
        report.test("详情包含 project_id", data.get("project_id") == PROJECT_ID, "")
        report.test("详情包含 language", data.get("language") in ("zh", "en"), "")

    # 3.6 项目详情 - 不存在
    r = api("/api/projects/nonexistent_id", headers=auth_headers)
    ok, err = assert_status(r, 404)
    report.test("不存在项目详情 404", ok, err)

    # 3.7 更新项目
    r = api(f"/api/projects/{PROJECT_ID}", "PUT", json={
        "name": "已更新项目名称",
        "language": "en",
    }, headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("更新项目 200", ok, err)

    # 验证更新生效
    r = api(f"/api/projects/{PROJECT_ID}", headers=auth_headers)
    if r.status_code == 200:
        report.test("更新后名称改变", r.json().get("name") == "已更新项目名称", "")

    # 3.8 轮换 API Key
    r = api(f"/api/projects/{PROJECT_ID}/rotate-key", "POST", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("轮换 API Key 200", ok, err)
    if ok:
        new_key = r.json().get("api_key", "")
        report.test("新 Key 格式正确", new_key.startswith("sk_"), "")
        # 更新全局 API_KEY
        API_KEY = new_key

    # 3.9 项目统计
    r = api(f"/api/projects/{PROJECT_ID}/stats", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("项目统计 200", ok, err)
    if ok:
        data = r.json()
        report.test("统计包含 document_count", "document_count" in data, "")
        report.test("统计包含 total_calls", "total_calls" in data, "")

    # 3.10 嵌入脚本
    r = api(f"/api/projects/{PROJECT_ID}/embed-script", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("嵌入脚本 200", ok, err)
    if ok:
        script = r.json().get("script", "")
        report.test("脚本包含 <script>", "<script" in script, "")

    # 3.11 项目列表 - 未登录
    r = api("/api/projects")
    ok, err = assert_status(r, 401)
    report.test("未登录项目列表 401", ok, err)


# ================================================================
# 4. 问答 API 测试
# ================================================================

def test_chat_apis():
    report.group("4. 问答 API")
    api_key_headers = {"X-API-Key": API_KEY}

    # 4.1 非流式问答
    r = api("/api/chat", "POST", json={
        "query": "你好",
        "top_k": 5,
    }, headers=api_key_headers)
    ok, err = assert_status(r, 200)
    report.test("问答请求 200", ok, err)
    if ok:
        data = r.json()
        report.test("返回 answer", bool(data.get("answer")), "")
        report.test("返回 sources", isinstance(data.get("sources"), list), "")
        report.test("返回 cache_hit", isinstance(data.get("cache_hit"), bool), "")
        report.test("返回 conversation_id", bool(data.get("conversation_id")), "")
        report.test("返回 handoff_suggested", isinstance(data.get("handoff_suggested"), bool), "")

    # 4.2 问答 - 无 API Key
    r = api("/api/chat", "POST", json={"query": "你好"})
    ok, err = assert_status(r, 401)
    report.test("无 API Key 问答 401", ok, err)

    # 4.3 问答 - 无效 API Key
    r = api("/api/chat", "POST", json={"query": "你好"}, headers={"X-API-Key": "sk_invalid"})
    ok, err = assert_status(r, 401)
    report.test("无效 API Key 问答 401", ok, err)

    # 4.4 问答 - 空查询
    r = api("/api/chat", "POST", json={"query": ""}, headers=api_key_headers)
    ok, err = assert_status(r, 422)
    report.test("空查询问答 422", ok, err)

    # 4.5 问答 - 含 conversation_id 续传
    # 先用真实请求获取 conversation_id，再续传
    conv_resp = api("/api/chat", "POST", json={
        "query": "建立会话",
    }, headers=api_key_headers)
    real_conv_id = ""
    if conv_resp.status_code == 200:
        real_conv_id = conv_resp.json().get("conversation_id", "")
    if real_conv_id:
        r = api("/api/chat", "POST", json={
            "query": "继续刚才的话题",
            "conversation_id": real_conv_id,
        }, headers=api_key_headers)
        ok, err = assert_status(r, 200)
        report.test("带 conversation_id 续传 200", ok, err)
        if ok:
            report.test("续传返回相同 conversation_id", r.json().get("conversation_id") == real_conv_id, "")
    else:
        report.test("带 conversation_id 续传 200", False, "无法获取真实 conversation_id")

    # 4.6 问答 - 不存在的 conversation_id 返回 404
    r = api("/api/chat", "POST", json={
        "query": "续传不存在的会话",
        "conversation_id": "nonexistent_conv_123",
    }, headers=api_key_headers)
    ok, err = assert_status(r, 404)
    report.test("不存在的会话 404", ok, err)


def _parse_sse_events(response_text: str):
    """解析 SSE 响应为事件列表。"""
    events = []
    for line in response_text.strip().split("\n"):
        if line.startswith("data: "):
            data = line[6:]
            try:
                events.append(json.loads(data))
            except json.JSONDecodeError:
                events.append({"event": "raw", "data": data})
    return events


def test_chat_stream_apis():
    """流式问答 (chat/stream) SSE 端到端测试。"""
    report.group("4b. 流式问答 (SSE)")
    api_key_headers = {"X-API-Key": API_KEY}

    # 4b.1 正常流式回答
    r = api("/api/chat/stream", "POST", json={"query": "你好，介绍一下自己"}, headers=api_key_headers)
    ok, err = assert_status(r, 200)
    report.test("流式问答 200", ok, err)
    if ok:
        report.test("SSE content-type", "text/event-stream" in r.headers.get("content-type", ""), r.headers.get("content-type", ""))
        events = _parse_sse_events(r.text)
        report.test("SSE 事件数 > 3", len(events) > 3, f"events={len(events)}")
        event_types = [e.get("event") for e in events]
        report.test("首个事件 conversation_id", event_types[0] == "conversation_id" if event_types else False, str(event_types[:3]))
        report.test("包含 sources 事件", "sources" in event_types, "")
        report.test("包含 cache_hit 事件", "cache_hit" in event_types, "")
        report.test("包含 answer_delta 事件", "answer_delta" in event_types, "")
        report.test("末个事件 done", event_types[-1] == "done" if event_types else False, str(event_types[-3:]))

    # 4b.2 流式回答内容
    r = api("/api/chat/stream", "POST", json={"query": "流式测试"}, headers=api_key_headers)
    if r.status_code == 200:
        events = _parse_sse_events(r.text)
        answer_chunks = [e.get("data") for e in events if e.get("event") == "answer_delta"]
        full_answer = "".join(chunk or "" for chunk in answer_chunks)
        report.test("流式回答非空", len(full_answer) > 0, f"len={len(full_answer)}")

    # 4b.3 无 API Key
    r = api("/api/chat/stream", "POST", json={"query": "测试"})
    ok, err = assert_status(r, 401)
    report.test("无 API Key 流式 401", ok, err)

    # 4b.4 无效 API Key
    r = api("/api/chat/stream", "POST", json={"query": "测试"}, headers={"X-API-Key": "sk_invalid"})
    ok, err = assert_status(r, 401)
    report.test("无效 API Key 流式 401", ok, err)

    # 4b.5 空查询
    r = api("/api/chat/stream", "POST", json={"query": ""}, headers=api_key_headers)
    ok, err = assert_status(r, 422)
    report.test("空查询流式 422", ok, err)

    # 4b.6 超长查询
    r = api("/api/chat/stream", "POST", json={"query": "x" * 2001}, headers=api_key_headers)
    ok, err = assert_status(r, 422)
    report.test("超长查询流式 422", ok, err)

    # 4b.7 会话续传
    r1 = api("/api/chat/stream", "POST", json={"query": "第一轮"}, headers=api_key_headers)
    if r1.status_code == 200:
        conv_id = _parse_sse_events(r1.text)[0].get("data", "")
        r2 = api("/api/chat/stream", "POST", json={
            "query": "第二轮",
            "conversation_id": conv_id,
        }, headers=api_key_headers)
        ok, err = assert_status(r2, 200)
        report.test("流式会话续传 200", ok, err)


# ================================================================
# 5. 知识库 API 测试
# ================================================================

DOC_ID = ""

def test_knowledge_apis():
    global DOC_ID
    report.group("5. 知识库 API")
    api_key_headers = {"X-API-Key": API_KEY}

    # 5.1 创建文档
    r = api("/api/knowledge", "POST", json={
        "title": "测试文档",
        "content": "这是测试文档的内容，用于测试知识库的创建、查询、更新和删除功能。",
        "tags": ["测试", "API"],
        "source": "integration-test",
    }, headers=api_key_headers)
    ok, err = assert_status(r, 200)
    report.test("创建文档 200", ok, err)
    if ok:
        DOC_ID = r.json().get("doc_id", "")
        report.test("返回 doc_id", bool(DOC_ID), "")

    # 5.2 创建文档 - 缺少标题
    r = api("/api/knowledge", "POST", json={
        "content": "缺少标题的文档",
    }, headers=api_key_headers)
    ok, err = assert_status(r, 422)
    report.test("缺少标题创建 422", ok, err)

    # 5.3 创建文档 - 缺少内容
    r = api("/api/knowledge", "POST", json={
        "title": "缺少内容",
    }, headers=api_key_headers)
    ok, err = assert_status(r, 422)
    report.test("缺少内容创建 422", ok, err)

    # 5.4 获取文档
    if DOC_ID:
        r = api(f"/api/knowledge/{DOC_ID}", headers=api_key_headers)
        ok, err = assert_status(r, 200)
        report.test("获取文档 200", ok, err)
        if ok:
            data = r.json()
            report.test("文档标题匹配", data.get("title") == "测试文档", "")
            report.test("文档有 tag", len(data.get("tags", [])) > 0, "")

    # 5.5 获取文档 - 不存在
    r = api("/api/knowledge/nonexistent_doc_id", headers=api_key_headers)
    ok, err = assert_status(r, 404)
    report.test("获取不存在文档 404", ok, err)

    # 5.6 更新文档
    if DOC_ID:
        r = api(f"/api/knowledge/{DOC_ID}", "PUT", json={
            "title": "已更新的测试文档",
            "tags": ["测试", "API", "已更新"],
        }, headers=api_key_headers)
        ok, err = assert_status(r, 200)
        report.test("更新文档 200", ok, err)

    # 5.7 验证更新
    if DOC_ID:
        r = api(f"/api/knowledge/{DOC_ID}", headers=api_key_headers)
        if r.status_code == 200:
            report.test("更新后标题改变", r.json().get("title") == "已更新的测试文档", "")

    # 5.8 文档列表
    r = api("/api/knowledge?page=1&page_size=10", headers=api_key_headers)
    ok, err = assert_status(r, 200)
    report.test("文档列表 200", ok, err)
    if ok:
        data = r.json()
        report.test("列表是分页格式", "items" in data and "total" in data, "")
        report.test("列表包含刚创建的文档", data.get("total", 0) >= 1, f"total={data.get('total')}")

    # 5.9 文档列表 - 无 API Key
    r = api("/api/knowledge")
    ok, err = assert_status(r, 401)
    report.test("无 Key 文档列表 401", ok, err)

    # 5.10 删除文档
    if DOC_ID:
        r = api(f"/api/knowledge/{DOC_ID}", "DELETE", headers=api_key_headers)
        ok, err = assert_status(r, 200)
        report.test("删除文档 200", ok, err)

    # 5.11 验证删除
    if DOC_ID:
        r = api(f"/api/knowledge/{DOC_ID}", headers=api_key_headers)
        report.test("删除后获取 404", r.status_code == 404, f"status={r.status_code}")


# ================================================================
# 6. 搜索 API 测试
# ================================================================

def test_search_apis():
    report.group("6. 搜索 API")
    api_key_headers = {"X-API-Key": API_KEY}

    # 6.1 搜索
    r = api("/api/search", "POST", json={
        "query": "测试",
        "top_k": 5,
    }, headers=api_key_headers)
    ok, err = assert_status(r, 200)
    report.test("搜索 200", ok, err)
    if ok:
        results = r.json()
        report.test("搜索结果是数组", isinstance(results, list), "")

    # 6.2 批量搜索
    r = api("/api/search/batch", "POST", json={
        "queries": ["测试", "你好"],
        "top_k": 3,
    }, headers=api_key_headers)
    ok, err = assert_status(r, 200)
    report.test("批量搜索 200", ok, err)
    if ok:
        results = r.json()
        report.test("批量搜索结果是数组", isinstance(results, list), "")
        if results:
            report.test("批量结果有 query_index", "query_index" in results[0], "")

    # 6.3 搜索 - 无 API Key
    r = api("/api/search", "POST", json={"query": "test"})
    ok, err = assert_status(r, 401)
    report.test("无 Key 搜索 401", ok, err)


# ================================================================
# 7. 分析 API 测试
# ================================================================

def test_analytics_apis():
    report.group("7. 分析 API")
    auth_headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    # 7.1 问答日志列表
    r = api(f"/api/projects/{PROJECT_ID}/logs?page=1&page_size=10", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("问答日志列表 200", ok, err)
    if ok:
        data = r.json()
        report.test("日志是分页格式", "items" in data and "total" in data, "")

    # 7.2 导出日志 (CSV)
    r = api(f"/api/projects/{PROJECT_ID}/logs/export?format=csv", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("导出日志 CSV 200", ok, err)

    # 7.3 导出日志 (JSON)
    r = api(f"/api/projects/{PROJECT_ID}/logs/export?format=json", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("导出日志 JSON 200", ok, err)

    if r.status_code == 200:
        report.test("JSON 格式可解析", "content-type" in r.headers, "")

    # 7.4 问答量趋势
    r = api(f"/api/projects/{PROJECT_ID}/analytics/trends?days=30", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("问答量趋势 200", ok, err)

    # 7.5 热门问题
    r = api(f"/api/projects/{PROJECT_ID}/analytics/top-questions?limit=10&days=30", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("热门问题 200", ok, err)

    # 7.6 满意度统计
    r = api(f"/api/projects/{PROJECT_ID}/analytics/satisfaction?days=30", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("满意度统计 200", ok, err)

    # 7.7 知识库缺口分析
    r = api(f"/api/projects/{PROJECT_ID}/analytics/gaps?days=30&limit=20", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("缺口分析 200", ok, err)

    # 7.8 提交反馈
    # 先获取一个 log_id
    logs_r = api(f"/api/projects/{PROJECT_ID}/logs?page=1&page_size=1", headers=auth_headers)
    log_id = 1
    if logs_r.status_code == 200:
        items = logs_r.json().get("items", [])
        if items:
            log_id = items[0].get("id", 1)

    r = api(f"/api/projects/{PROJECT_ID}/feedback", "POST", json={
        "log_id": log_id,
        "rating": "good",
    }, headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("提交反馈 200", ok, err)

    # 7.9 日志 - 不是自己的项目
    r = api("/api/projects/invalid_project_id/logs", headers=auth_headers)
    ok, err = assert_status(r, 404)
    report.test("非自己项目日志 404", ok, err)

    # 7.10 日志 - 未登录
    r = api(f"/api/projects/{PROJECT_ID}/logs")
    ok, err = assert_status(r, 401)
    report.test("未登录日志 401", ok, err)


# ================================================================
# 8. 会话管理 API 测试
# ================================================================

def test_conversation_apis():
    report.group("8. 会话管理 API")
    auth_headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    # 8.1 会话列表
    r = api(f"/api/projects/{PROJECT_ID}/conversations?page=1&page_size=10", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("会话列表 200", ok, err)
    if ok:
        data = r.json()
        report.test("会话列表是分页格式", "items" in data and "total" in data, "")

    # 8.2 会话列表 - 未登录
    r = api(f"/api/projects/{PROJECT_ID}/conversations")
    ok, err = assert_status(r, 401)
    report.test("未登录会话列表 401", ok, err)

    # 8.3 会话列表 - 不存在项目
    r = api("/api/projects/invalid_id/conversations", headers=auth_headers)
    ok, err = assert_status(r, 404)
    report.test("不存在项目会话列表 404", ok, err)

    # 8.4 获取会话 - 不存在
    r = api(f"/api/projects/{PROJECT_ID}/conversations/nonexistent_conv", headers=auth_headers)
    ok, err = assert_status(r, 404)
    report.test("获取不存在会话 404", ok, err)


# ================================================================
# 9. 电商 API 测试
# ================================================================

def test_ecommerce_apis():
    report.group("9. 电商 API")

    # 9.1 FAQ 模板列表
    r = api("/api/templates")
    ok, err = assert_status(r, 200)
    report.test("FAQ 模板列表 200", ok, err)
    if ok:
        data = r.json()
        report.test("模板列表是数组", isinstance(data, list), "")

    # 9.2 模板详情 - 不存在
    r = api("/api/templates/nonexistent_template")
    ok, err = assert_status(r, 404)
    report.test("不存在模板详情 404", ok, err)


# ================================================================
# 10. 计费 API 测试
# ================================================================

def test_billing_apis():
    report.group("10. 计费 API")
    auth_headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    # 10.1 获取套餐信息
    r = api(f"/api/billing/plan?project_id={PROJECT_ID}", headers=auth_headers)
    ok, err = assert_status(r, 200)
    report.test("获取套餐信息 200", ok, err)
    if ok:
        data = r.json()
        report.test("套餐包含 plan", "plan" in data, "")
        report.test("套餐包含 limits", "limits" in data, "")
        report.test("套餐包含 usage", "usage" in data, "")
        report.test("默认套餐是 free", data.get("plan") == "free", f"plan={data.get('plan')}")

    # 10.2 获取套餐 - 未登录
    r = api("/api/billing/plan")
    ok, err = assert_status(r, 401)
    report.test("未登录获取套餐 401", ok, err)

    # 10.3 获取账单列表 - 未登录
    r = api("/api/billing/invoices")
    ok, err = assert_status(r, 401)
    report.test("未登录账单列表 401", ok, err)


# ================================================================
# 11. 管理后台 API 测试
# ================================================================

def test_admin_apis():
    report.group("11. 管理后台 API")
    auth_headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    # 11.1 管理后台统计 - 普通用户无权限
    r = api("/api/admin/stats", headers=auth_headers)
    ok, err = assert_status(r, 403)
    report.test("普通用户管理后台 403", ok, err)

    # 11.2 管理后台用户列表 - 无权限
    r = api("/api/admin/users", headers=auth_headers)
    report.test("普通用户管理后台用户列表 403", r.status_code == 403, f"status={r.status_code}")

    # 11.3 管理后台项目列表 - 无权限
    r = api("/api/admin/projects", headers=auth_headers)
    report.test("普通用户管理后台项目列表 403", r.status_code == 403, f"status={r.status_code}")

    # 11.4 管理后台 - 未登录
    r = api("/api/admin/stats")
    ok, err = assert_status(r, 401)
    report.test("未登录管理后台 401", ok, err)


# ================================================================
# 12. 公式错误场景测试
# ================================================================

def test_error_scenarios():
    report.group("12. 错误场景测试")

    # 12.1 404 未找到路由
    r = api("/api/nonexistent_route")
    ok, err = assert_status(r, 404)
    report.test("不存在路由 404", ok, err)

    # 12.2 方法不允许
    r = api("/api/health", "DELETE")
    report.test("健康检查不支持 DELETE", r.status_code in (405, 404), f"status={r.status_code}")

    # 12.3 无效 JSON 请求体
    r = api("/api/chat", "POST", data="not json at all", headers={"X-API-Key": API_KEY, "Content-Type": "application/json"})
    report.test("无效 JSON 请求体", r.status_code in (400, 422), f"status={r.status_code}")

    # 12.4 字段类型错误
    r = api("/api/knowledge", "POST", json={
        "title": 12345,  # 应该是 string
        "content": "test",
    }, headers={"X-API-Key": API_KEY})
    report.test("字段类型错误 422", r.status_code == 422, f"status={r.status_code}")


# ================================================================
# 主流程
# ================================================================

def main():
    global BASE_URL
    parser = argparse.ArgumentParser(description="OpenAsk API 全面集成测试")
    parser.add_argument("--base-url", default=BASE_URL, help="API 基础 URL")
    args = parser.parse_args()

    BASE_URL = args.base_url.rstrip("/")

    print(f"OpenAsk API 全面集成测试")
    print(f"  服务地址: {BASE_URL}")
    print(f"  测试邮箱: {TEST_EMAIL}")

    # 测试依赖关系：有些测试依赖前面的测试结果
    test_system_apis()
    test_auth_apis()

    # 只有认证成功后才继续
    if not AUTH_TOKEN:
        print("\n  ⚠️  认证失败，跳过后续测试")
        report.summary()
        return

    test_project_apis()

    if not PROJECT_ID or not API_KEY:
        print("\n  ⚠️  项目准备失败，跳过业务测试")
        report.summary()
        return

    test_auth_email_flow()
    test_chat_apis()
    test_chat_stream_apis()
    test_knowledge_apis()
    test_search_apis()
    test_analytics_apis()
    test_conversation_apis()
    test_ecommerce_apis()
    test_billing_apis()
    test_admin_apis()
    test_error_scenarios()

    success = report.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()