"""OpenAsk Python SDK

轻量级 SDK，用于与 OpenAsk API 交互。

安装:
    pip install requests

使用:
    from openask import OpenAsk
    client = OpenAsk(api_key="sk_xxx")
    resp = client.chat("退货政策是什么？")
"""

from .client import OpenAsk

__all__ = ["OpenAsk"]