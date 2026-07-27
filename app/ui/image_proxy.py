from __future__ import annotations

from urllib.parse import quote, urlsplit

import httpx
from fastapi import HTTPException, Query
from fastapi.responses import Response
from nicegui import app


ROUTE = "/_preview/wechat-image"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_HOSTS = {"mmbiz.qpic.cn"}


def wechat_image_proxy_url(source_url: str) -> str:
    """Return a local URL so the browser never hotlinks WeChat's image CDN."""
    return f"{ROUTE}?url={quote(str(source_url or ''), safe='')}"


def validate_wechat_image_url(source_url: str) -> str:
    value = str(source_url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("只允许代理微信图片素材地址")
    return "https://" + value.split("://", 1)[1]


@app.get(ROUTE, include_in_schema=False)
async def proxy_wechat_image(url: str = Query(min_length=1, max_length=4096)) -> Response:
    try:
        source_url = validate_wechat_image_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            upstream = await client.get(source_url)
            upstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="微信图片素材读取失败") from exc
    content_type = str(upstream.headers.get("content-type") or "").split(";", 1)[0]
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=502, detail="微信素材返回的不是图片")
    if len(upstream.content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="微信图片素材超过 10MB")
    return Response(
        content=upstream.content,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )
