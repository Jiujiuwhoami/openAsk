# 修复 Windows 下 slowapi/starlette Config 读取 .env 的编码问题：
# Config._read_file 用 open() 无编码参数，默认 cp936(GBK)，
# 但 .env 是 UTF-8 含中文，会导致 UnicodeDecodeError。
# 在 import slowapi 之前 monkeypatch Config._read_file，强制使用 UTF-8。
original_read_file = None
def _read_file_utf8(self, file_name):
    from pathlib import Path
    file_values = {}
    path = Path(file_name)
    if path.exists():
        with open(path, encoding="utf-8") as f:
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

limiter = Limiter(key_func=get_remote_address)
