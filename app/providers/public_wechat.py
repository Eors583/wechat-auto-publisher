from __future__ import annotations

import html as html_module
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .article_search import resolve_article_url


_TRACKING_PARAMS = {
    "ascene", "chksm", "clicktime", "devicetype", "enterid", "exportkey",
    "forceh5", "from", "isappinstalled", "lang", "pass_ticket", "scene",
    "session_us", "subscene", "version", "wx_header",
}


def normalize_article_url(url: str) -> str:
    # Decode URL separators narrowly. html.unescape() turns ``&timestamp``
    # into ``×tamp`` because ``&times`` is an HTML entity, invalidating
    # signed WeChat links returned by public search engines.
    value = (
        (url or "").strip()
        .replace("&amp;", "&")
        .replace("&#38;", "&")
        .replace("&#x26;", "&")
        .replace("&#X26;", "&")
        .rstrip("。；，,;")
    )
    if not value:
        return ""
    parsed = urlsplit(value)
    scheme = (parsed.scheme or "https").lower()
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    query = parse_qsl(parsed.query, keep_blank_values=False)
    if host.endswith("mp.weixin.qq.com"):
        scheme = "https"
        query = [(key, val) for key, val in query if key not in _TRACKING_PARAMS]
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))


def fetch_public_article_metadata(url: str, *, timeout: float = 18.0) -> dict[str, Any]:
    resolved = resolve_article_url(url, timeout=min(timeout, 12.0))
    normalized = normalize_article_url(resolved)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        response = client.get(resolved)
        response.raise_for_status()
        page = response.text or ""
        final_url = normalize_article_url(str(response.url)) or normalized

    title = _meta(page, "og:title") or _first_group(
        page,
        r"(?:var\s+)?msg_title\s*=\s*['\"]([^'\"]+)",
        r"<title[^>]*>(.*?)</title>",
    )
    account_name = _first_group(
        page,
        r"(?:var\s+)?nickname\s*=\s*['\"]([^'\"]+)",
        r'<strong[^>]+class="[^"]*profile_nickname[^"]*"[^>]*>(.*?)</strong>',
        r'<a[^>]+id="js_name"[^>]*>(.*?)</a>',
    ) or _meta(page, "og:article:author")
    cover = _meta(page, "og:image") or _first_group(
        page, r"(?:var\s+)?msg_cdn_url\s*=\s*['\"]([^'\"]+)"
    )
    summary = _meta(page, "og:description") or _meta(page, "description")
    timestamp = _first_group(
        page,
        r"(?:var\s+)?ct\s*=\s*['\"]?(\d{10})",
        r"(?:publish_time|create_time)\s*[:=]\s*['\"]?(\d{10})",
    )
    published_at = ""
    if timestamp and timestamp.isdigit():
        published_at = datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat()
    parsed = urlsplit(final_url)
    query = dict(parse_qsl(parsed.query))
    page_biz = _first_group(
        page,
        r'(?:var\s+)?biz\s*=\s*["\']([^"\']+)',
        r'(?:var\s+)?user_name\s*=\s*["\']([^"\']+)',
    )
    page_mid = _first_group(
        page,
        r'(?:var\s+)?mid\s*=\s*["\'](\d+)',
        r'(?:var\s+)?appmsgid\s*=\s*["\'](\d+)',
    )
    page_idx = _first_group(
        page,
        r'(?:var\s+)?idx\s*=\s*["\'](\d+)',
        r'(?:var\s+)?itemidx\s*=\s*["\'](\d+)',
    )
    external_key = "|".join(
        (
            str(query.get("__biz") or page_biz or ""),
            str(query.get("mid") or page_mid or ""),
            str(query.get("idx") or page_idx or ""),
        )
    ).strip("|")
    return {
        "title": _clean(title),
        "account_name": _clean(account_name),
        "url": final_url,
        "published_at": published_at,
        "cover_url": html_module.unescape(cover.strip()) if cover else "",
        "summary": _clean(summary)[:500],
        "external_key": external_key,
    }


def _meta(page: str, name: str) -> str:
    escaped = re.escape(name)
    patterns = (
        rf'<meta[^>]+(?:property|name)=["\']{escaped}["\'][^>]+content=["\']([^"\']*)["\']',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{escaped}["\']',
    )
    return _first_group(page, *patterns)


def _first_group(page: str, *patterns: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, page or "", flags=re.I | re.S)
        if match:
            return html_module.unescape(str(match.group(1) or "")).strip()
    return ""


def _clean(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html_module.unescape(text)).strip()
