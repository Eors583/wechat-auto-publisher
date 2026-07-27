from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus, unquote

import httpx

logger = logging.getLogger(__name__)


def search_weixin_articles(
    keyword: str,
    *,
    limit: int = 3,
    timeout: float = 15.0,
    account_name: str = "",
) -> list[dict[str, str]]:
    """按关键词检索相关微信文章，返回最多 limit 条 {title, url, snippet}。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []

    # 优先搜狗微信；失败再试百度 site 检索
    results = _search_sogou(
        keyword,
        limit=limit,
        timeout=timeout,
        expected_account_name=account_name,
    )
    if len(results) < limit:
        extra = _search_baidu_weixin(keyword, limit=limit, timeout=timeout)
        seen = {r["url"] for r in results}
        for row in extra:
            if row["url"] not in seen:
                results.append(row)
                seen.add(row["url"])
            if len(results) >= limit:
                break
    return results[:limit]


def search_weixin_account_articles(
    account_name: str,
    *,
    wechat_id: str = "",
    limit: int = 8,
    timeout: float = 15.0,
) -> list[dict[str, str]]:
    """Best-effort recent public posts for one named WeChat account."""
    name = (account_name or "").strip()
    if not name:
        return []
    rows = _search_sogou_account_latest(
        name,
        wechat_id=wechat_id,
        timeout=timeout,
    )
    rows.extend(
        search_weixin_articles(
            name,
            limit=max(limit, 10),
            timeout=timeout,
            account_name=name,
        )
    )
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        url = str(row.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        output.append(row)
        if len(output) >= limit:
            break
    return output


def _search_sogou(
    keyword: str,
    *,
    limit: int,
    timeout: float,
    expected_account_name: str = "",
) -> list[dict[str, str]]:
    url = f"https://weixin.sogou.com/weixin?type=2&s_from=input&query={quote_plus(keyword)}&ie=utf8"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://weixin.sogou.com/",
    }
    try:
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                return []
            html = resp.text
            search_cookies = dict(client.cookies)
    except Exception as exc:  # noqa: BLE001
        logger.info("Sogou search failed: %s", exc)
        return []

    # 验证码/封锁页
    if "antispider" in html or "验证码" in html:
        logger.info("Sogou antispider triggered")
        return []

    resolver = httpx.Client(
        timeout=timeout,
        headers=headers,
        follow_redirects=True,
        cookies=search_cookies,
    )
    results: list[dict[str, str]] = []
    # 标题与链接常见结构：<a ... href="..." ... uigs="article_title_0">标题</a>
    pattern = re.compile(
        r'<a[^>]+href="([^"]+)"[^>]*?uigs="article_title_\d+"[^>]*>(.*?)</a>',
        re.I | re.S,
    )
    for match in pattern.finditer(html):
        href, raw_title = match.group(1), match.group(2)
        block_start = html.rfind("<li", 0, match.start())
        block_end = html.find("</li>", match.end())
        block = html[block_start:block_end] if block_start >= 0 and block_end >= 0 else ""
        publisher_match = re.search(
            r'<div[^>]+class="[^"]*s-p[^"]*"[^>]*>[\s\S]*?<span[^>]*>([\s\S]*?)</span>',
            block,
            flags=re.I,
        )
        publisher = _strip_html(publisher_match.group(1)) if publisher_match else ""
        if expected_account_name and publisher and not _same_account_name(expected_account_name, publisher):
            continue
        timestamp_match = re.search(r"timeConvert\('(\d{10})'\)", block)
        published_at = ""
        if timestamp_match:
            published_at = datetime.fromtimestamp(
                int(timestamp_match.group(1)), timezone.utc
            ).isoformat()
        title = _strip_html(raw_title)
        link = href.replace("&amp;", "&").strip()
        if not title or len(title) < 4 or title in {"搜狗搜索", "微信搜索"}:
            continue
        if "javascript:" in link:
            continue
        # 搜狗中转链 → 尽量还原真实公众号 URL
        real = _unwrap_sogou_link(link)
        if not real:
            if link.startswith("//"):
                real = "https:" + link
            elif link.startswith("/"):
                real = "https://weixin.sogou.com" + link
            else:
                real = link
        if "mp.weixin.qq.com" not in real and "/link?url=" not in real:
            continue
        direct = _resolve_sogou_link_with_client(resolver, real)
        if "mp.weixin.qq.com/s" not in direct:
            continue
        results.append(
            {
                "title": title,
                "url": direct,
                "snippet": "",
                "account_name": publisher,
                "published_at": published_at,
            }
        )
        if len(results) >= limit:
            break

    # 备选解析：txt-box 列表
    if not results:
        blocks = re.findall(r'<div class="txt-box">([\s\S]*?)</div>\s*</li>', html)
        for block in blocks:
            m_a = re.search(r'<h3>[\s\S]*?<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>', block)
            if not m_a:
                continue
            title = _strip_html(m_a.group(2))
            link = _unwrap_sogou_link(m_a.group(1).replace("&amp;", "&")) or m_a.group(1)
            direct = _resolve_sogou_link_with_client(resolver, link)
            if title and "mp.weixin.qq.com/s" in direct:
                results.append({"title": title, "url": direct, "snippet": ""})
            if len(results) >= limit:
                break
    resolver.close()
    return results


def _search_sogou_account_latest(
    account_name: str,
    *,
    wechat_id: str,
    timeout: float,
) -> list[dict[str, str]]:
    url = (
        "https://weixin.sogou.com/weixin?type=1&s_from=input&query="
        f"{quote_plus(account_name)}&ie=utf8"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://weixin.sogou.com/",
    }
    try:
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            response = client.get(url)
            page = response.text or ""
            if response.status_code >= 400 or "antispider" in page:
                return []
            blocks = re.findall(
                r'<li[^>]*>([\s\S]*?)</li>', page, flags=re.I
            )
            for block in blocks:
                name_match = re.search(
                    r'<p[^>]+class="[^"]*tit[^"]*"[^>]*>[\s\S]*?<a[^>]*>([\s\S]*?)</a>',
                    block,
                    flags=re.I,
                )
                id_match = re.search(
                    r'<p[^>]+class="[^"]*info[^"]*"[^>]*>[\s\S]*?<label[^>]*>([\s\S]*?)</label>',
                    block,
                    flags=re.I,
                )
                found_name = _strip_html(name_match.group(1)) if name_match else ""
                found_id = _strip_html(id_match.group(1)) if id_match else ""
                if not _same_account_name(account_name, found_name):
                    continue
                if wechat_id and found_id and not _same_account_name(wechat_id, found_id):
                    continue
                latest = re.search(
                    r'<dt>\s*最近文章\s*</dt>[\s\S]*?<dd[^>]*>[\s\S]*?<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>',
                    block,
                    flags=re.I,
                )
                if not latest:
                    continue
                direct = _resolve_sogou_link_with_client(
                    client, latest.group(1).replace("&amp;", "&")
                )
                if "mp.weixin.qq.com/s" not in direct:
                    continue
                timestamp_match = re.search(r"timeConvert\('(\d{10})'\)", block)
                published_at = ""
                if timestamp_match:
                    published_at = datetime.fromtimestamp(
                        int(timestamp_match.group(1)), timezone.utc
                    ).isoformat()
                return [
                    {
                        "title": _strip_html(latest.group(2)),
                        "url": direct,
                        "snippet": "",
                        "account_name": found_name,
                        "published_at": published_at,
                    }
                ]
    except Exception as exc:  # noqa: BLE001
        logger.info("Sogou account search failed: %s", exc)
    return []


def _search_baidu_weixin(keyword: str, *, limit: int, timeout: float) -> list[dict[str, str]]:
    q = f"site:mp.weixin.qq.com {keyword}"
    url = f"https://www.baidu.com/s?wd={quote_plus(q)}&rn=10"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    try:
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                return []
            html = resp.text
    except Exception as exc:  # noqa: BLE001
        logger.info("Baidu search failed: %s", exc)
        return []

    results: list[dict[str, str]] = []
    # 直链或百度跳转中的公众号链接
    for m in re.finditer(
        r'https?://mp\.weixin\.qq\.com/s[^\s"\'<>]+|href="(/link\?url=[^"]+)"',
        html,
    ):
        raw = m.group(0)
        if raw.startswith("href="):
            continue
        link = raw.replace("&amp;", "&")
        # 从周边截一点标题很不稳定，用关键词+序号兜底
        title = f"{keyword} · 相关文章 {len(results) + 1}"
        # 尝试回退找附近标题
        start = max(0, m.start() - 400)
        chunk = html[start : m.start()]
        tm = re.search(r">([^<]{8,60})</a>\s*$", chunk)
        if tm:
            title = _strip_html(tm.group(1))
        if "mp.weixin.qq.com/s" not in link:
            continue
        results.append({"title": title, "url": link.split("&")[0], "snippet": ""})
        if len(results) >= limit:
            break
    return results


def _unwrap_sogou_link(link: str) -> str | None:
    # .../link?url=ENCODED
    m = re.search(r"[?&]url=([^&]+)", link)
    if m:
        try:
            decoded = unquote(m.group(1))
            if decoded.startswith("http"):
                return decoded
        except Exception:  # noqa: BLE001
            return None
    if link.startswith("http") and "mp.weixin.qq.com" in link:
        return link
    return None


def resolve_article_url(link: str, *, timeout: float = 12.0) -> str:
    """把搜狗中转链等尽量解析成可直接打开的公众号文章 URL。"""
    link = (link or "").strip()
    if not link:
        return link
    unwrapped = _unwrap_sogou_link(link) or link
    if "mp.weixin.qq.com/s" in unwrapped:
        return unwrapped.split("#")[0]
    if "weixin.sogou.com" not in unwrapped and "mp.weixin.qq.com" not in unwrapped:
        return unwrapped
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://weixin.sogou.com/",
    }
    try:
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            resolved = _resolve_sogou_link_with_client(client, unwrapped)
            if "mp.weixin.qq.com/s" in resolved:
                return resolved
    except Exception as exc:  # noqa: BLE001
        logger.info("resolve_article_url failed: %s", exc)
    return unwrapped


def _resolve_sogou_link_with_client(client: httpx.Client, link: str) -> str:
    """Resolve a Sogou result in the same cookie session used for searching.

    Sogou's intermediate page assembles the WeChat URL through repeated
    ``url += '...'`` statements. Following redirects alone therefore leaves
    callers with a Sogou URL even when the page is otherwise usable.
    """
    value = _unescape_url((link or "").strip())
    if value.startswith("//"):
        value = "https:" + value
    elif value.startswith("/"):
        value = "https://weixin.sogou.com" + value
    if "mp.weixin.qq.com/s" in value:
        return value.split("#")[0]
    if "weixin.sogou.com" not in value:
        return value
    try:
        response = client.get(value)
    except Exception as exc:  # noqa: BLE001
        logger.info("resolve Sogou article failed: %s", exc)
        return value
    final = str(response.url)
    if "mp.weixin.qq.com/s" in final:
        return final.split("#")[0]
    page = response.text or ""
    reconstructed = _reconstruct_sogou_article_url(page)
    if reconstructed:
        return reconstructed
    match = re.search(r'https?://mp\.weixin\.qq\.com/s[^\s"\'<>]+', page)
    if match:
        return _unescape_url(match.group(0)).split("#")[0]
    return value


def _reconstruct_sogou_article_url(page: str) -> str:
    parts = re.findall(r"url\s*\+=\s*['\"](.*?)['\"]\s*;", page or "", flags=re.S)
    if not parts:
        return ""
    value = _unescape_url("".join(parts).replace("@", "").strip())
    return value.split("#")[0] if "mp.weixin.qq.com/s" in value else ""


def _unescape_url(value: str) -> str:
    """Decode URL separators without treating ``&timestamp`` as ``&times``."""
    return (
        (value or "")
        .replace("&amp;", "&")
        .replace("&#38;", "&")
        .replace("&#x26;", "&")
        .replace("&#X26;", "&")
    )


def _same_account_name(left: str, right: str) -> bool:
    normalize = lambda value: re.sub(r"\s+", "", value or "").casefold()
    return bool(normalize(left)) and normalize(left) == normalize(right)


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = (
        text.replace("&ldquo;", "“")
        .replace("&rdquo;", "”")
        .replace("&mdash;", "—")
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def attach_top_articles(
    topics: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """给话题列表补齐 articles 字段（同步，偏慢；UI 侧可对单条懒加载）。"""
    out: list[dict[str, Any]] = []
    for row in topics:
        title = str(row.get("title") or row.get("topic") or "").strip()
        item = dict(row)
        item["title"] = title
        if title:
            item["articles"] = search_weixin_articles(title, limit=limit)
        else:
            item["articles"] = []
        out.append(item)
    return out
