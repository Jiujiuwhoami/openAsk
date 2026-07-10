"""API 层：对外提供 HTTP 接口。"""

from .main import app, lifespan

__all__ = ["app", "lifespan"]