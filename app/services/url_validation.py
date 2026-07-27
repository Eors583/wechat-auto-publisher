from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def validate_external_url(value: str) -> None:
    """Reject malformed URLs and SSRF targets before article ingestion."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("只支持有效的 http/https 文章链接")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("不允许访问本机或内网地址")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(hostname, parsed.port or 443)
        }
    except socket.gaierror as exc:
        raise ValueError(f"链接域名无法解析：{hostname}") from exc
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise ValueError("不允许访问本机或内网地址")
