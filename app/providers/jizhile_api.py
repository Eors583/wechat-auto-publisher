from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx

JIZHILE_BASE_URL = "https://www.dajiala.com"
POST_HISTORY_PATH = "/fbmain/monitor/v3/post_history"
BALANCE_PATH = "/fbmain/monitor/v3/get_remain_money"

_TRANSIENT_CODES = {-1, 111, 112, 2003, 2005, 50000}
_NO_MORE_ARTICLES_CODES = {110, 115}
_ERROR_MESSAGES = {
    -1: "极致了 API 请求频率超过 5 次/秒，请稍后重试",
    101: "文章已删除，或公众号已封禁/迁移",
    104: "公众号原始 ID 不存在",
    105: "公众号查询参数无效，请使用公众号名称、原始 ID 或示例文章链接",
    110: "没有更多历史文章，或接口暂时未返回数据",
    111: "极致了 API 请求过于频繁，请稍后重试",
    112: "极致了 API 请求失败，请稍后重试",
    113: "极致了 API 鉴权失败，请稍后重试",
    115: "当前页及后续文章已全部删除",
    400: "公众号文章短链接转换失败，请改用长链接或公众号名称",
    2003: "极致了 API 系统资源请求出错，请稍后重试",
    2005: "极致了 API 系统错误，请稍后重试",
    10002: "极致了 API Key 或附加码不正确",
    20001: "极致了 API 余额不足，请充值后重试",
    20002: "示例文章链接不是有效的微信文章链接",
    20003: "文章链接格式有误，请检查链接参数",
    30001: "未提供可识别的公众号名称、原始 ID 或文章链接",
    50000: "极致了 API 内部服务器错误，请稍后重试",
    "mode=2023": "该公众号已禁止搜索或已注销/封禁，请改用示例文章链接或其他数据源",
}


class JizhileApiError(RuntimeError):
    """The Jizhile API rejected or could not complete a request."""

    def __init__(self, message: str, *, code: int | str | None = None) -> None:
        super().__init__(message)
        self.code = code


def fetch_jizhile_account_articles(
    account_name: str,
    *,
    key: str,
    verifycode: str = "",
    wechat_id: str = "",
    sample_url: str = "",
    limit: int = 8,
    timeout: float = 25.0,
) -> list[dict[str, str]]:
    """Fetch recent public posts for one exact account from Jizhile.

    Jizhile accepts a public-account name, original ``gh_`` id, or an article
    URL. An existing sample article URL is the strongest identity, followed by
    an original id. The account name is the safe default for ordinary WeChat
    aliases because the API's ``wxid`` is not always the public alias shown to
    operators.
    """

    name = str(account_name or "").strip()
    api_key = str(key or "").strip()
    if not name:
        raise ValueError("公众号名称不能为空")
    if not api_key:
        raise JizhileApiError("请先配置极致了 API Key")

    requested = max(1, min(int(limit or 8), 100))
    identity = _identity_payload(
        account_name=name,
        wechat_id=wechat_id,
        sample_url=sample_url,
    )
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    offset = ""

    with _client(timeout=timeout) as client:
        # The API returns several publication batches per page. Five pages is
        # enough for the UI's cumulative window while avoiding accidental
        # high-cost unbounded collection.
        for _ in range(5):
            payload = _post_json(
                client,
                POST_HISTORY_PATH,
                {
                    **identity,
                    "offset": offset,
                    "key": api_key,
                    "verifycode": str(verifycode or "").strip(),
                },
            )
            code = _response_code(payload)
            if code in _NO_MORE_ARTICLES_CODES:
                break
            _raise_for_api_error(payload, action="获取公众号历史文章")

            account_label = str(
                payload.get("mp_nickname")
                or payload.get("nickname")
                or name
            ).strip()
            data = payload.get("data") or []
            if not isinstance(data, list):
                raise JizhileApiError("极致了 API 返回的文章列表格式异常")
            before = len(rows)
            for item in data:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                title = str(item.get("title") or "").strip()
                if not url or "mp.weixin.qq.com" not in url or not title:
                    continue
                identity_key = str(
                    item.get("sn")
                    or (
                        f'{item.get("appmsgid") or ""}:{item.get("position") or ""}'
                        if item.get("appmsgid")
                        else ""
                    )
                    or url
                ).strip()
                if identity_key in seen:
                    continue
                seen.add(identity_key)
                rows.append(
                    {
                        "title": title,
                        "url": url,
                        "snippet": str(item.get("digest") or "").strip(),
                        "cover_url": str(
                            item.get("cover_url")
                            or item.get("pic_cdn_url_16_9")
                            or item.get("pic_cdn_url_235_1")
                            or ""
                        ).strip(),
                        "account_name": account_label,
                        "wechat_id": str(
                            payload.get("mp_wxid") or wechat_id or ""
                        ).strip(),
                        "published_at": _published_at(item),
                        "external_key": identity_key,
                    }
                )
                if len(rows) >= requested:
                    break

            if len(rows) >= requested or len(rows) == before:
                break
            next_offset = str(
                payload.get("offset") or payload.get("next_offset") or ""
            ).strip()
            if (
                not next_offset
                or next_offset == offset
                or _is_truthy(payload.get("is_end"))
            ):
                break
            offset = next_offset

    rows.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    return rows[:requested]


def test_jizhile_api(
    *,
    key: str,
    verifycode: str = "",
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Validate API credentials using the provider's balance endpoint."""

    api_key = str(key or "").strip()
    if not api_key:
        raise JizhileApiError("请先填写极致了 API Key")
    with _client(timeout=timeout) as client:
        payload = _post_json(
            client,
            BALANCE_PATH,
            {
                "key": api_key,
                "verifycode": str(verifycode or "").strip(),
            },
        )
    _raise_for_api_error(payload, action="验证极致了 API")
    return {
        "ok": True,
        "remain_money": payload.get("remain_money"),
        "yesterday_money": payload.get("yesterday_money"),
        "request_time": str(payload.get("request_time") or ""),
    }


def _identity_payload(
    *,
    account_name: str,
    wechat_id: str,
    sample_url: str,
) -> dict[str, str]:
    article_url = str(sample_url or "").strip()
    account_id = str(wechat_id or "").strip()
    if article_url:
        return {"url": article_url}
    if account_id.casefold().startswith("gh_"):
        return {"ghid": account_id}
    # post_history v3 accepts ``nickname`` rather than the older generic
    # ``name`` field. Sending ``name`` produces provider error 105 even when
    # the account and balance are valid.
    return {"nickname": str(account_name or "").strip()}


def _client(*, timeout: float) -> httpx.Client:
    return httpx.Client(
        base_url=JIZHILE_BASE_URL,
        timeout=timeout,
        follow_redirects=True,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "wechat-auto-publisher/1.3",
        },
    )


def _post_json(
    client: httpx.Client,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client.post(path, json=payload)
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise JizhileApiError("极致了 API 返回格式异常")
            code = _response_code(value)
            if code in _TRANSIENT_CODES and attempt < 2:
                time.sleep(5.0 if code == -1 else (2.0 if code == 2005 else 1.0))
                continue
            return value
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if int(exc.response.status_code) < 500 or attempt >= 2:
                raise JizhileApiError(
                    f"极致了 API 返回 HTTP {exc.response.status_code}"
                ) from exc
        except httpx.RequestError as exc:
            last_error = exc
            if attempt >= 2:
                raise JizhileApiError(
                    "无法连接极致了 API，请检查网络或代理后重试"
                ) from exc
        except ValueError as exc:
            last_error = exc
            if attempt >= 2:
                raise JizhileApiError("极致了 API 返回了无效的 JSON") from exc
        time.sleep(float(attempt + 1))
    raise JizhileApiError("极致了 API 请求失败") from last_error


def _raise_for_api_error(payload: dict[str, Any], *, action: str) -> None:
    code = _response_code(payload)
    if code == 0:
        return
    message = _ERROR_MESSAGES.get(code)
    if not message:
        message = str(
            payload.get("msg")
            or payload.get("message")
            or payload.get("error_msg")
            or "未知错误"
        ).strip()
        message = f"{action}失败（{code}）：{message}"
    raise JizhileApiError(message, code=code)


def _response_code(payload: dict[str, Any]) -> int | str:
    if str(payload.get("mode") or "").strip() == "2023":
        return "mode=2023"
    raw = payload.get("code", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return str(raw or "")


def _published_at(item: dict[str, Any]) -> str:
    raw = item.get("post_time") or item.get("pre_post_time")
    try:
        timestamp = int(raw or 0)
    except (TypeError, ValueError):
        timestamp = 0
    if timestamp > 0:
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
    return ""


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}
