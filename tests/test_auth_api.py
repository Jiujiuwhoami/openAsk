"""Auth API 端点测试 — 注册、登录、Token、邮箱验证、密码重置。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    from src.api.auth import router
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


# 所有测试用例使用唯一邮箱，避免数据库冲突
_EMAIL_COUNTER = 0


def unique_email(prefix="test"):
    global _EMAIL_COUNTER
    _EMAIL_COUNTER += 1
    return f"{prefix}_{_EMAIL_COUNTER}@test.com"


class TestRegister:
    def test_register_success(self, client):
        resp = client.post("/api/auth/register", json={
            "email": unique_email(), "password": "password123", "name": "新用户",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "project" in data

    def test_register_duplicate(self, client):
        email = unique_email()
        client.post("/api/auth/register", json={"email": email, "password": "password123"})
        resp = client.post("/api/auth/register", json={"email": email, "password": "password123"})
        assert resp.status_code == 409

    def test_register_short_password(self, client):
        resp = client.post("/api/auth/register", json={
            "email": unique_email(), "password": "123",
        })
        assert resp.status_code == 422

    def test_register_invalid_email(self, client):
        resp = client.post("/api/auth/register", json={
            "email": "not-an-email", "password": "password123",
        })
        assert resp.status_code == 422

    def test_register_minimal(self, client):
        resp = client.post("/api/auth/register", json={
            "email": unique_email(), "password": "password123",
        })
        assert resp.status_code == 200


class TestLogin:
    def test_login_success(self, client):
        email = unique_email()
        client.post("/api/auth/register", json={"email": email, "password": "password123"})
        resp = client.post("/api/auth/token", data={"username": email, "password": "password123"})
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_login_wrong_password(self, client):
        email = unique_email()
        client.post("/api/auth/register", json={"email": email, "password": "correctpwd"})
        resp = client.post("/api/auth/token", data={"username": email, "password": "wrongpwd"})
        assert resp.status_code == 401

    def test_login_nonexistent(self, client):
        resp = client.post("/api/auth/token", data={
            "username": unique_email(), "password": "password123",
        })
        assert resp.status_code == 401

    def test_login_form_encoded(self, client):
        email = unique_email()
        client.post("/api/auth/register", json={"email": email, "password": "password123"})
        resp = client.post(
            "/api/auth/token",
            content=f"username={email}&password=password123",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200


class TestMe:
    def test_me_success(self, client):
        email = unique_email()
        reg = client.post("/api/auth/register", json={
            "email": email, "password": "password123",
        }).json()
        resp = client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {reg['access_token']}",
        })
        assert resp.status_code == 200
        assert resp.json()["email"] == email

    def test_me_no_token(self, client):
        assert client.get("/api/auth/me").status_code == 401

    def test_me_invalid_token(self, client):
        assert client.get("/api/auth/me", headers={
            "Authorization": "Bearer invalid",
        }).status_code == 401


class TestChangePassword:
    def test_change_password_success(self, client):
        email = unique_email()
        reg = client.post("/api/auth/register", json={
            "email": email, "password": "oldpassword",
        }).json()
        resp = client.post(
            "/api/auth/change-password",
            json={"old_password": "oldpassword", "new_password": "newpassword"},
            headers={"Authorization": f"Bearer {reg['access_token']}"},
        )
        assert resp.status_code == 200

    def test_change_password_wrong_old(self, client):
        email = unique_email()
        reg = client.post("/api/auth/register", json={
            "email": email, "password": "correctpwd",
        }).json()
        resp = client.post(
            "/api/auth/change-password",
            json={"old_password": "wrongold", "new_password": "newpassword"},
            headers={"Authorization": f"Bearer {reg['access_token']}"},
        )
        assert resp.status_code == 401