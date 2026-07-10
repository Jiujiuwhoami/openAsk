"""限流配置：全局共享的 Limiter 实例。"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
