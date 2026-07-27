from __future__ import annotations

from urllib.parse import urlsplit

import httpx


MAX_FEISHU_IMAGE_BYTES = 10 * 1024 * 1024
WECHAT_IMAGE_HOSTS = {"mmbiz.qpic.cn"}


def download_wechat_image(url: str) -> tuple[bytes, str]:
    """Download one trusted WeChat CDN image for re-upload to Feishu."""

    value = str(url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in WECHAT_IMAGE_HOSTS:
        raise ValueError("只允许读取微信图片素材地址")
    safe_url = "https://" + value.split("://", 1)[1]
    with httpx.Client(
        timeout=20.0,
        follow_redirects=False,
        headers={"User-Agent": "Mozilla/5.0 (WeChatAutoPublisher/1.0)"},
    ) as client:
        response = client.get(safe_url)
        response.raise_for_status()
    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0]
    if not content_type.startswith("image/"):
        raise ValueError("微信素材返回的不是图片")
    if len(response.content) > MAX_FEISHU_IMAGE_BYTES:
        raise ValueError("微信图片素材超过 10MB")
    extension = {
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(content_type, "jpg")
    return response.content, extension
