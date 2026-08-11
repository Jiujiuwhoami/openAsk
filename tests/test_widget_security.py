"""Widget 安全测试：Token 机制、域名白名单、权限隔离。

覆盖：
  1. Domain 工具函数（parse_host / is_domain_allowed）
  2. Widget Token 签发/验证（JWT type=widget）
  3. Widget Session 端点（Origin 白名单校验）
  4. 权限隔离（widget token 不能访问知识库 CRUD）
  5. 嵌入脚本不含 API Key
"""

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.services.widget_token import create_widget_token, verify_widget_token
from src.services.user_service import UserService
from src.utils.domain import parse_host, is_domain_allowed


# ================================================================
# Domain 工具测试
# ================================================================

class TestDomainUtils:
    def test_parse_host_standard(self):
        assert parse_host("https://www.example.com") == "www.example.com"

    def test_parse_host_default_port_stripped(self):
        """默认端口 443/80 不保留。"""
        assert parse_host("https://www.example.com:443") == "www.example.com"
        assert parse_host("http://example.com:80") == "example.com"

    def test_parse_host_non_default_port(self):
        """非默认端口保留。"""
        assert parse_host("http://localhost:5173") == "localhost:5173"
        assert parse_host("https://shop.example.com:8080/path") == "shop.example.com:8080"

    def test_parse_host_empty(self):
        assert parse_host("") == ""

    def test_parse_host_referer(self):
        assert parse_host("http://localhost:5173/settings") == "localhost:5173"

    def test_parse_host_no_scheme(self):
        """没有 scheme 的 URL 返回空。"""
        assert parse_host("localhost:5173") == ""

    def test_is_domain_allowed_exact(self):
        assert is_domain_allowed("example.com", ["example.com"]) is True

    def test_is_domain_allowed_subdomain(self):
        assert is_domain_allowed("www.example.com", ["example.com"]) is True
        assert is_domain_allowed("shop.example.com", ["example.com"]) is True
        assert is_domain_allowed("deep.sub.example.com", ["example.com"]) is True

    def test_is_domain_allowed_not_match(self):
        assert is_domain_allowed("notexample.com", ["example.com"]) is False
        assert is_domain_allowed("example.com", ["shop.example.com"]) is False

    def test_is_domain_allowed_with_port(self):
        """带端口的域名必须精确匹配。"""
        assert is_domain_allowed("localhost:5173", ["localhost:5173"]) is True
        assert is_domain_allowed("localhost:3000", ["localhost:5173"]) is False

    def test_is_domain_allowed_empty_list(self):
        assert is_domain_allowed("example.com", []) is False

    def test_is_domain_allowed_empty_host(self):
        assert is_domain_allowed("", ["example.com"]) is False


# ================================================================
# Widget Token 测试
# ================================================================

class TestWidgetToken:
    def test_create_and_verify(self):
        token = create_widget_token("proj_test123")
        assert verify_widget_token(token) == "proj_test123"

    def test_custom_expiry(self):
        token = create_widget_token("proj_test123", expires_minutes=5)
        assert verify_widget_token(token) == "proj_test123"

    def test_forged_token(self):
        assert verify_widget_token("forged.token.here") is None
        assert verify_widget_token("") is None
        assert verify_widget_token(None) is None  # type: ignore

    def test_user_jwt_rejected(self):
        """用户 JWT（type=access）不能通过 widget token 验证。"""
        user_token = UserService.create_access_token("user_test123")
        assert verify_widget_token(user_token) is None

    def test_expired_token(self):
        """过期 token 被拒绝。"""
        import jwt as pyjwt
        from datetime import datetime, timedelta
        from src.utils.config import settings

        expired = pyjwt.encode(
            {"sub": "proj_x", "type": "widget", "exp": datetime.utcnow() - timedelta(minutes=1)},
            settings.auth.secret_key, algorithm="HS256",
        )
        assert verify_widget_token(expired) is None


# ================================================================
# Widget Session 端点测试
# ================================================================

@pytest.fixture
def widget_client():
    """包含 widget session 端点的测试应用。"""
    import secrets
    from src.utils.limiter import limiter
    from src.api.widget import router
    from src.services.project_service import ProjectService

    unique_key = "sk_test_widget_" + secrets.token_hex(4)
    empty_key = "sk_empty_" + secrets.token_hex(4)

    svc = ProjectService()
    project = svc.create_project(
        user_id="test_user",
        name="Widget Test",
        api_key=unique_key,
        allowed_domains=["example.com", "shop.example.com"],
    )
    empty_project = svc.create_project(
        user_id="test_user",
        name="Empty Domains",
        api_key=empty_key,
        allowed_domains=[],
    )

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(router)

    with TestClient(app) as client:
        client._proj_id = project.project_id
        client._empty_proj_id = empty_project.project_id
        yield client


class TestWidgetSession:
    def test_session_success(self, widget_client):
        """有效项目 + 允许的 origin → 200 + token。"""
        resp = widget_client.post(
            "/api/widget/session",
            json={"project_id": widget_client._proj_id},
            headers={"Origin": "https://example.com"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "token" in data
        assert data["expires_in"] > 0
        assert data["project_id"] == widget_client._proj_id

    def test_session_subdomain_origin(self, widget_client):
        """子域名 origin 也匹配白名单。"""
        resp = widget_client.post(
            "/api/widget/session",
            json={"project_id": widget_client._proj_id},
            headers={"Origin": "https://www.example.com"},
        )
        assert resp.status_code == 200, resp.text

    def test_session_no_origin(self, widget_client):
        """无 Origin 头 → 403。"""
        resp = widget_client.post(
            "/api/widget/session",
            json={"project_id": widget_client._proj_id},
        )
        assert resp.status_code == 403, resp.text

    def test_session_disallowed_origin(self, widget_client):
        """不允许的 origin → 403。"""
        resp = widget_client.post(
            "/api/widget/session",
            json={"project_id": widget_client._proj_id},
            headers={"Origin": "https://evil.com"},
        )
        assert resp.status_code == 403, resp.text

    def test_session_nonexistent_project(self, widget_client):
        """不存在的项目 → 404。"""
        resp = widget_client.post(
            "/api/widget/session",
            json={"project_id": "proj_nonexistent"},
            headers={"Origin": "https://example.com"},
        )
        assert resp.status_code == 404, resp.text

    def test_session_empty_allowed_domains(self, widget_client):
        """空白名单 + 非 frontend origin → 403（fail closed）。"""
        resp = widget_client.post(
            "/api/widget/session",
            json={"project_id": widget_client._empty_proj_id},
            headers={"Origin": "https://evil.com"},
        )
        assert resp.status_code == 403, resp.text
        assert "域名" in resp.text or "配置" in resp.text

    def test_session_empty_allowed_domains_frontend(self, widget_client):
        """空白名单 + frontend origin → 200（管理后台预览场景）。"""
        from src.utils.config import settings
        frontend = settings.api.frontend_url
        resp = widget_client.post(
            "/api/widget/session",
            json={"project_id": widget_client._empty_proj_id},
            headers={"Origin": frontend},
        )
        assert resp.status_code == 200, resp.text
        assert "token" in resp.json()


# ================================================================
# 权限隔离测试（使用与 test_api.py 相同的 mock 模式）
# ================================================================

class TestPermissionIsolation:
    def test_widget_token_cannot_access_knowledge(self, widget_client):
        """Widget token 不是有效的 X-API-Key → 知识库 API 拒绝（401）。"""
        resp = widget_client.post(
            "/api/widget/session",
            json={"project_id": widget_client._proj_id},
            headers={"Origin": "https://example.com"},
        )
        assert resp.status_code == 200
        token = resp.json()["token"]

        # 直接验证 widget token 不能当作 API Key 使用
        from src.services.project_service import ProjectService
        project = ProjectService().get_by_api_key(token)
        assert project is None, "widget token 不应被当作 API Key 使用"

    def test_widget_token_not_api_key(self, widget_client):
        """Widget token 不能当作 API Key 使用。"""
        from src.services.project_service import ProjectService
        # 创建一个有效的 widget token
        from src.services.widget_token import create_widget_token
        token = create_widget_token(widget_client._proj_id)
        project = ProjectService().get_by_api_key(token)
        assert project is None, "widget token 不应被当作 API Key 使用"

    def test_script_contains_no_api_key(self):
        """嵌入脚本不包含 API Key。"""
        from src.services.embed_script import generate_embed_script

        script = generate_embed_script("proj_test123", api_base="http://localhost:8000")
        # 检查真正的 API Key 格式（sk_ + 48 hex chars）
        api_key_pattern = re.compile(r"sk_[0-9a-f]{20,}")
        assert not api_key_pattern.search(script), "脚本中泄露了 API Key"
        assert "X-Widget-Token" in script, "脚本应使用 X-Widget-Token"
        assert "/api/widget/session" in script, "脚本应换取 token"
        assert "X-API-Key" not in script, "脚本不应使用 X-API-Key"

    def test_project_allowed_domains_roundtrip(self):
        """Project allowed_domains 存取往返。"""
        from src.services.project_service import ProjectService

        svc = ProjectService()
        project = svc.create_project(
            user_id="test_user",
            name="Domains Roundtrip",
            api_key="sk_roundtrip_test",
            allowed_domains=["one.com", "two.com"],
        )
        fetched = svc.get_by_id(project.project_id)
        assert fetched.allowed_domains == ["one.com", "two.com"]

        # 更新
        svc.update_project(project.project_id, allowed_domains=["three.com"])
        fetched = svc.get_by_id(project.project_id)
        assert fetched.allowed_domains == ["three.com"]