from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx


MP_BASE_URL = "https://mp.weixin.qq.com"


class WechatBackendSearchError(RuntimeError):
    """The WeChat public-platform management session could not serve a search."""


def search_backend_account_articles(
    account_name: str,
    *,
    token: str,
    cookie: str,
    wechat_id: str = "",
    limit: int = 8,
    timeout: float = 20.0,
) -> list[dict[str, str]]:
    """Search an account and its posts through the logged-in MP editor.

    The protocol mirrors the search feature available inside the editor of an
    account managed by the user. It deliberately lives behind a provider
    boundary because these web-management endpoints are not part of the
    official public API and can change independently of the rest of the app.

    Protocol reference (MIT):
    https://github.com/wechat-article/wechat-article-exporter
    """

    name = (account_name or "").strip()
    if not name:
        raise ValueError("公众号名称不能为空")
    token = normalize_backend_token(token)
    cookie = normalize_backend_cookie(cookie)
    if not token or not cookie:
        raise WechatBackendSearchError("公众号后台搜索尚未配置 Token 和 Cookie")

    page_size = max(1, min(int(limit or 8), 20))
    with _client(cookie=cookie, timeout=timeout) as client:
        account = _find_account(
            client,
            name,
            token=token,
            wechat_id=wechat_id,
        )
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        begin = 0
        # Limit the number of management-platform requests even if a malformed
        # response keeps reporting more data.
        for _ in range(5):
            payload = _get_json(
                client,
                "/cgi-bin/appmsgpublish",
                params={
                    "sub": "list",
                    "search_field": "null",
                    "begin": begin,
                    "count": page_size,
                    "query": "",
                    "fakeid": str(account.get("fakeid") or ""),
                    "type": "101_1",
                    "free_publish_type": 1,
                    "sub_action": "list_ex",
                    "token": token,
                    "lang": "zh_CN",
                    "f": "json",
                    "ajax": 1,
                },
            )
            _raise_for_mp_error(payload, action="获取公众号文章")
            publish_page = _json_object(payload.get("publish_page"), "文章列表")
            publish_list = publish_page.get("publish_list") or []
            if not isinstance(publish_list, list) or not publish_list:
                break
            received_count = len(publish_list)
            before = len(rows)
            for group in publish_list:
                if not isinstance(group, dict) or not group.get("publish_info"):
                    continue
                publish_info = _json_object(group.get("publish_info"), "文章详情")
                articles = publish_info.get("appmsgex") or []
                if not isinstance(articles, list):
                    continue
                for article in articles:
                    if not isinstance(article, dict):
                        continue
                    url = html.unescape(str(article.get("link") or "")).strip()
                    title = html.unescape(str(article.get("title") or "")).strip()
                    if not url or "mp.weixin.qq.com" not in url or not title:
                        continue
                    identity = str(article.get("aid") or "").strip() or url
                    if identity in seen:
                        continue
                    seen.add(identity)
                    timestamp = _positive_int(
                        article.get("create_time") or article.get("update_time")
                    )
                    rows.append(
                        {
                            "title": title,
                            "url": url,
                            "snippet": html.unescape(
                                str(article.get("digest") or "")
                            ).strip(),
                            "cover_url": html.unescape(
                                str(
                                    article.get("cover")
                                    or article.get("cover_img")
                                    or article.get("pic_cdn_url_16_9")
                                    or ""
                                )
                            ).strip(),
                            "account_name": str(account.get("nickname") or name),
                            "wechat_id": str(account.get("alias") or ""),
                            "published_at": (
                                datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
                                if timestamp
                                else ""
                            ),
                            "external_key": identity,
                        }
                    )
                    if len(rows) >= limit:
                        break
                if len(rows) >= limit:
                    break
            if (
                len(rows) >= limit
                or len(rows) == before
                or received_count < page_size
            ):
                break
            begin += page_size
    rows.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    return rows[:limit]


def test_backend_session(
    *,
    token: str,
    cookie: str,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Validate that the configured management-platform session is usable."""

    token = normalize_backend_token(token)
    cookie = normalize_backend_cookie(cookie)
    if not token or not cookie:
        raise WechatBackendSearchError("请先填写公众号后台 Token 和 Cookie")
    with _client(cookie=cookie, timeout=timeout) as client:
        payload = _get_json(
            client,
            "/cgi-bin/searchbiz",
            params={
                "action": "search_biz",
                "begin": 0,
                "count": 1,
                "query": "微信公众平台",
                "token": token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": 1,
            },
        )
        _raise_for_mp_error(payload, action="验证公众号后台会话")
    return {"ok": True, "result_count": int(payload.get("total") or 0)}


def _find_account(
    client: httpx.Client,
    account_name: str,
    *,
    token: str,
    wechat_id: str,
) -> dict[str, Any]:
    payload = _get_json(
        client,
        "/cgi-bin/searchbiz",
        params={
            "action": "search_biz",
            "begin": 0,
            "count": 10,
            "query": account_name,
            "token": token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        },
    )
    _raise_for_mp_error(payload, action="搜索公众号")
    accounts = payload.get("list") or []
    if not isinstance(accounts, list):
        accounts = []
    expected_name = _name_key(account_name)
    expected_id = _name_key(wechat_id)
    exact = [
        row
        for row in accounts
        if isinstance(row, dict)
        and _name_key(str(row.get("nickname") or "")) == expected_name
    ]
    if expected_id:
        exact_id = [
            row
            for row in exact
            if _name_key(str(row.get("alias") or "")) == expected_id
        ]
        if exact_id:
            return exact_id[0]
    if exact:
        return exact[0]
    names = "、".join(
        str(row.get("nickname") or "").strip()
        for row in accounts[:5]
        if isinstance(row, dict) and str(row.get("nickname") or "").strip()
    )
    suffix = f"；搜索结果为：{names}" if names else ""
    raise WechatBackendSearchError(
        f"公众号后台未找到完全匹配的“{account_name}”{suffix}"
    )


def _client(*, cookie: str, timeout: float) -> httpx.Client:
    return httpx.Client(
        base_url=MP_BASE_URL,
        timeout=timeout,
        follow_redirects=True,
        headers={
            "Cookie": cookie,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://mp.weixin.qq.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )


def _get_json(
    client: httpx.Client,
    path: str,
    *,
    params: dict[str, str | int],
) -> dict[str, Any]:
    try:
        response = client.get(path, params=params)
        response.raise_for_status()
        value = response.json()
    except httpx.HTTPStatusError as exc:
        status_code = int(exc.response.status_code)
        raise WechatBackendSearchError(
            f"公众号后台请求返回 HTTP {status_code}，请确认登录态仍有效后重试"
        ) from exc
    except httpx.RequestError as exc:
        raise WechatBackendSearchError(
            "无法连接微信公众号后台，请检查本机网络或代理后重试"
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise WechatBackendSearchError(
            "公众号后台返回了非 JSON 内容，登录态可能已经失效"
        ) from exc
    if not isinstance(value, dict):
        raise WechatBackendSearchError("公众号后台返回格式异常")
    return value


def _raise_for_mp_error(payload: dict[str, Any], *, action: str) -> None:
    base = payload.get("base_resp") or {}
    if not isinstance(base, dict):
        return
    ret = int(base.get("ret") or 0)
    if ret == 0:
        return
    message = str(base.get("err_msg") or base.get("errmsg") or "未知错误")
    if ret == 200003:
        raise WechatBackendSearchError(
            "公众号后台登录态已过期，请重新登录后更新 Token 和 Cookie"
        )
    raise WechatBackendSearchError(f"{action}失败（{ret}）：{message}")


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError as exc:
        raise WechatBackendSearchError(f"{label}解析失败") from exc
    if not isinstance(parsed, dict):
        raise WechatBackendSearchError(f"{label}格式异常")
    return parsed


def normalize_backend_token(value: str) -> str:
    """Accept either the numeric token or a copied MP backend URL."""

    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"(?:^|[?&#\s])token=([A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]+", text):
        return text
    raise WechatBackendSearchError(
        "Token 格式不正确：请粘贴后台地址栏中的完整链接，"
        "或只粘贴链接里 token= 后面的内容"
    )


def normalize_backend_cookie(value: str) -> str:
    """Accept a Cookie value, a ``Cookie:`` line, or copied request headers."""

    text = str(value or "").strip()
    if not text:
        return ""
    cookie_line = next(
        (
            line.split(":", 1)[1].strip()
            for line in text.splitlines()
            if line.strip().casefold().startswith("cookie:")
        ),
        "",
    )
    if cookie_line:
        text = cookie_line
    elif "\n" in text or "\r" in text:
        raise WechatBackendSearchError(
            "Cookie 格式不正确：如果复制了整段请求头，请确保其中包含 Cookie: 这一行"
        )
    text = re.sub(r"^cookie\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\r\n]+", " ", text).strip()
    if "=" not in text:
        raise WechatBackendSearchError(
            "Cookie 格式不正确：请复制 Request Headers 中 Cookie: 后面的完整内容"
        )
    return text


def _name_key(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0
