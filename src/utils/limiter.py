# 修复 Windows 下 slowapi/starlette Config 读取 .env 的编码问题：
# Config._read_file 用 open() 无编码参数，默认 cp936(GBK)，
# 但 .env 是 UTF-8 含中文，会导致 UnicodeDecodeError。
# 在 import slowapi 之前 monkeypatch Config._read_file，强制使用 UTF-8。
original_read_file = None
def _read_file_utf8(self, file_name, encoding="utf-8"):
    from pathlib import Path
    file_values = {}
    path = Path(file_name)
    if path.exists():
        with open(path, encoding=encoding) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    file_values[key.strip()] = value.strip().strip("\"'")
    return file_values

# 延迟 import starlette.config，避免循环导入
import starlette.config
original_read_file = starlette.config.Config._read_file
starlette.config.Config._read_file = _read_file_utf8

"""限流配置：全局共享的 Limiter 实例。"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from src.utils.config import settings


def _key_func(request):
    """优先读 X-Forwarded-For（代理/网关场景），降级到 socket IP。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # 可能有多个 IP，取第一个（最左边的客户端）
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


# 支持 Redis 存储（多 Worker 共享限流状态）：
#   memory://                    内存（默认，单进程）
#   redis://redis-host:6379/0    Redis（生产环境，多进程共享）
storage_uri = settings.rate_limit.storage_uri
limiter = Limiter(
    key_func=_key_func,
    storage_uri=storage_uri if storage_uri and storage_uri != "memory://" else None,
    in_memory_fallback_enabled=True,  # Redis 不可用时降级到内存
)
