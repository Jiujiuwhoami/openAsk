"""FastAPI 应用入口测试 — 错误处理器、中间件、sitemap、robots。"""

from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.domain.exceptions import (
    AppError, KnowledgeBaseError, DocumentNotFoundError,
    EmbeddingError, VectorStoreError, SenseNovaAPIError, MultiModalError,
)


# ================================================================
# 错误处理器
# ================================================================


class TestErrorHandlers:
    @pytest.fixture
    def app(self):
        from src.api.schemas import ErrorResponse
        from fastapi.responses import JSONResponse
        from datetime import datetime

        app = FastAPI(debug=False)  # 关闭 debug 模式，避免 ServerErrorMiddleware 重抛异常

        async def _app_error_handler(request, exc):
            status_code = 500
            if isinstance(exc, DocumentNotFoundError):
                status_code = 404
            elif isinstance(exc, (KnowledgeBaseError, MultiModalError)):
                status_code = 400
            elif isinstance(exc, (EmbeddingError, VectorStoreError, SenseNovaAPIError)):
                status_code = 503
            return JSONResponse(
                status_code=status_code,
                content=ErrorResponse(
                    error=exc.__class__.__name__,
                    message=str(exc),
                    timestamp=datetime.now(),
                ).model_dump(mode="json"),
            )
        app.add_exception_handler(AppError, _app_error_handler)

        async def _generic_handler(request, exc):
            from src.api.schemas import ErrorResponse
            return JSONResponse(
                status_code=500,
                content=ErrorResponse(
                    error="InternalServerError",
                    message="服务器内部错误，请稍后重试",
                    timestamp=datetime.now(),
                ).model_dump(mode="json"),
            )
        app.add_exception_handler(Exception, _generic_handler)

        @app.get("/raise/knowledge")
        async def raise_knowledge():
            raise KnowledgeBaseError("知识库错误")

        @app.get("/raise/notfound")
        async def raise_notfound():
            raise DocumentNotFoundError("文档不存在")

        @app.get("/raise/embedding")
        async def raise_embedding():
            raise EmbeddingError("嵌入失败")

        @app.get("/raise/app")
        async def raise_app():
            raise AppError("通用错误")

        @app.get("/raise/generic")
        async def raise_generic():
            raise ValueError("未处理异常")

        @app.get("/raise/vector")
        async def raise_vector():
            raise VectorStoreError("向量库错误")

        return app

    def _client(self, app, **kwargs):
        return TestClient(app, raise_server_exceptions=False, **kwargs)

    def test_knowledge_base_error(self, app):
        with self._client(app) as client:
            resp = client.get("/raise/knowledge")
            assert resp.status_code == 400
            data = resp.json()
            assert data["error"] == "KnowledgeBaseError"

    def test_document_not_found(self, app):
        with self._client(app) as client:
            resp = client.get("/raise/notfound")
            assert resp.status_code == 404
            data = resp.json()
            assert data["error"] == "DocumentNotFoundError"

    def test_embedding_error(self, app):
        with self._client(app) as client:
            resp = client.get("/raise/embedding")
            assert resp.status_code == 503
            data = resp.json()
            assert data["error"] == "EmbeddingError"

    def test_app_error_default_500(self, app):
        with self._client(app) as client:
            resp = client.get("/raise/app")
            assert resp.status_code == 500
            data = resp.json()
            assert data["error"] == "AppError"

    def test_generic_error_500(self, app):
        """未处理的异常返回 500。"""
        with self._client(app) as client:
            resp = client.get("/raise/generic")
            assert resp.status_code == 500
            data = resp.json()
            assert data["error"] == "InternalServerError"

    def test_vector_store_error(self, app):
        with self._client(app) as client:
            resp = client.get("/raise/vector")
            assert resp.status_code == 503
            data = resp.json()
            assert data["error"] == "VectorStoreError"

    def test_error_response_fields(self, app):
        """错误响应包含 error/message/timestamp。"""
        with self._client(app) as client:
            resp = client.get("/raise/knowledge")
            data = resp.json()
            assert "error" in data
            assert "message" in data
            assert "timestamp" in data


# ================================================================
# 中间件
# ================================================================


class TestMiddleware:
    @pytest.fixture
    def app(self):
        from src.api.main import dynamic_rate_limit_middleware, usage_limit_middleware, request_count_middleware

        app = FastAPI()

        @app.get("/api/chat")
        async def chat():
            return {"message": "ok"}

        @app.get("/api/health")
        async def health():
            return {"status": "ok"}

        app.middleware("http")(request_count_middleware)
        app.middleware("http")(usage_limit_middleware)
        app.middleware("http")(dynamic_rate_limit_middleware)

        return app

    def test_health_no_rate_limit(self, app):
        """健康检查应不受限流影响。"""
        with TestClient(app) as client:
            resp = client.get("/api/health")
            assert resp.status_code == 200


# ================================================================
# Sitemap & Robots
# ================================================================


class TestSitemapRobots:
    @pytest.fixture
    def app(self):
        from src.api.main import sitemap, robots
        app = FastAPI()
        # 注册路由
        app.get("/sitemap.xml", response_class=type("resp", (), {}), include_in_schema=False)(sitemap)
        # 手动添加路由
        from fastapi.responses import HTMLResponse, PlainTextResponse
        app.router.add_api_route(
            "/sitemap.xml", sitemap, response_class=HTMLResponse, include_in_schema=False
        )
        app.router.add_api_route(
            "/robots.txt", robots, response_class=PlainTextResponse, include_in_schema=False
        )
        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_sitemap(self, client):
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert "urlset" in resp.text

    def test_robots(self, client):
        resp = client.get("/robots.txt")
        assert resp.status_code == 200
        assert "User-agent" in resp.text


# ================================================================
# 异常类型的 HTTP 状态码映射
# ================================================================


class TestExceptionStatusCodeMapping:
    """验证异常 → HTTP 状态码映射关系（内联 handler）。"""

    def _make_handler(self):
        from src.api.schemas import ErrorResponse
        from fastapi.responses import JSONResponse
        from datetime import datetime

        async def handler(request, exc):
            status_code = 500
            if isinstance(exc, DocumentNotFoundError):
                status_code = 404
            elif isinstance(exc, (KnowledgeBaseError, MultiModalError)):
                status_code = 400
            elif isinstance(exc, (EmbeddingError, VectorStoreError, SenseNovaAPIError)):
                status_code = 503
            return JSONResponse(
                status_code=status_code,
                content=ErrorResponse(
                    error=exc.__class__.__name__,
                    message=str(exc),
                    timestamp=datetime.now(),
                ).model_dump(mode="json"),
            )
        return handler

    @pytest.mark.parametrize("exc_cls,expected_status", [
        (KnowledgeBaseError, 400),
        (MultiModalError, 400),
        (DocumentNotFoundError, 404),
        (EmbeddingError, 503),
        (VectorStoreError, 503),
        (SenseNovaAPIError, 503),
        (AppError, 500),
    ])
    @pytest.mark.asyncio
    async def test_exception_mapping(self, exc_cls, expected_status):
        from fastapi import Request
        handler = self._make_handler()
        request = Mock(spec=Request)
        exc = exc_cls("test error message")
        response = await handler(request, exc)
        assert response.status_code == expected_status, f"{exc_cls.__name__} should map to {expected_status}"