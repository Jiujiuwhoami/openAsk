"""域名匹配工具函数。

用于 Widget 嵌入脚本的 Origin 校验：
- 将 Origin/Referer header 解析为 hostname
- 与 Project 域名白名单进行匹配（精确匹配或子域名匹配）
"""

from urllib.parse import urlparse


def parse_host(origin: str) -> str:
    """从 Origin 或 Referer URL 提取 hostname（带端口）。

    Origin 格式: "https://www.example.com:443"
    Referer 格式: "https://www.example.com:443/foo/bar"

    解析结果示例:
      "https://www.example.com"       → "www.example.com"
      "https://www.example.com:443"   → "www.example.com"
      "http://localhost:5173"          → "localhost:5173"
      "http://localhost:5173/settings" → "localhost:5173"
      ""                               → ""

    Returns:
        提取的 hostname（含端口，如果有非标准端口）。空字符串原样返回。
    """
    origin = origin.strip()
    if not origin:
        return ""

    parsed = urlparse(origin)
    hostname = parsed.hostname or ""

    # 只在非标准端口时保留端口信息
    port = parsed.port
    if port is not None:
        # 80 或 443 是默认端口，不保留
        if port not in (80, 443):
            hostname = f"{hostname}:{port}"

    return hostname


def is_domain_allowed(host: str, allowed_domains: list[str]) -> bool:
    """检查 host 是否在域名白名单中。

    匹配规则（按优先级）：
    1. 精确匹配: host == allowed_domain
    2. 子域名匹配: 如果 allowed_domain 不含端口，则 host 是 allowed_domain 的子域名也匹配
       "example.com" → "www.example.com" ✅, "shop.example.com" ✅
    3. 带端口域名: 必须精确匹配（常用于开发环境 localhost:5173）

    Args:
        host: 请求来源的 hostname（由 parse_host 提取）
        allowed_domains: 域名白名单列表

    Returns:
        True 如果 host 在白名单中，否则 False
    """
    if not host or not allowed_domains:
        return False

    host = host.lower().strip()

    for allowed in allowed_domains:
        allowed = allowed.lower().strip()
        if not allowed:
            continue

        # 精确匹配
        if host == allowed:
            return True

        # 子域名匹配: 仅当 allowed 不含端口时
        if ":" not in allowed:
            # host 以 ".allowed" 结尾 → 子域名匹配
            if host.endswith("." + allowed):
                return True

    return False