"""Validate a single proxy endpoint for import and native browser launches."""

import re
from ipaddress import IPv4Address, IPv6Address


def normalize_host(value: str) -> str:
    if not isinstance(value, str) or not value.isascii():
        raise ValueError("主机格式无效")
    host = value.strip().lower()
    if ":" in host:
        address = host[1:-1] if host.startswith("[") and host.endswith("]") else host
        if "%" in address:
            raise ValueError("不支持带区域标识的 IPv6 地址")
        try:
            return f"[{IPv6Address(address)}]"
        except ValueError:
            raise ValueError("IPv6 地址无效") from None
    host = host.removesuffix(".")
    labels = host.split(".")
    if re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]*)", labels[-1]):
        try:
            return str(IPv4Address(host))
        except ValueError:
            raise ValueError("IPv4 地址无效") from None
    if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise ValueError("主机格式无效")
    return host


def chromium_proxy_server(proxy) -> str:
    # Credentials are consumed by the loopback proxy bridge, never by the
    # Chromium command line. This helper only validates the upstream endpoint.
    protocol = proxy["protocol"]
    port = proxy["port"]
    if protocol not in ("http", "https", "socks5"):
        raise ValueError("Chromium 代理协议须为 HTTP、HTTPS 或 SOCKS5")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("代理端口须在 1 至 65535 之间")
    return f"{protocol}://{normalize_host(proxy['host'])}:{port}"
