from __future__ import annotations

import base64
import hashlib
import html as html_module
import json
import re
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlsplit

import httpx

from app.db import Database, customer_data_scope

SOURCE_TYPES = {
    "rss": "行业网站 RSS",
    "news_search": "新闻搜索",
    "hot_api": "热点接口",
    "manual": "手动选题库",
    "followed_accounts": "关注公众号",
}

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_BUILTIN_HOT_SOURCES = {
    "hot-weibo": {
        "url": "https://weibo.com/ajax/side/hotSearch",
        "provider": "weibo",
    },
    "hot-baidu": {
        "url": "https://top.baidu.com/api/board?platform=wise&tab=realtime",
        "provider": "baidu",
    },
}


class TopicSourceService:
    """Persist, refresh and query independent topic sources."""

    def __init__(self, db: Database, config: dict[str, Any]) -> None:
        self.db = db
        self.config = config
        self._defaults_ensured_for: set[str] = set()

    def ensure_defaults(self) -> None:
        owner_key = self.db.owner_user_id or "__unscoped__"
        if owner_key in self._defaults_ensured_for:
            return
        topics = dict(self.config.get("topics") or {})
        defaults: list[dict[str, Any]] = [
            {
                "id": "internal-followed-accounts",
                "name": "关注公众号",
                "source_type": "followed_accounts",
                "config": {},
                "enabled": True,
            },
            {
                "id": "internal-manual-topics",
                "name": "手动选题库",
                "source_type": "manual",
                "config": {},
                "enabled": True,
            },
        ]
        for row in topics.get("rss_sources") or []:
            if not isinstance(row, dict) or not str(row.get("url") or "").strip():
                continue
            name = str(row.get("name") or "行业资讯").strip()
            url = str(row["url"]).strip()
            defaults.append(
                {
                    "id": "rss-" + _short_hash(url),
                    "name": name,
                    "source_type": "rss",
                    "config": {
                        "url": url,
                        "query_param": str(row.get("query_param") or ""),
                    },
                    "enabled": bool(row.get("enabled", True)),
                }
            )
        queries = [str(item).strip() for item in topics.get("news_queries") or [] if str(item).strip()]
        if queries:
            defaults.append(
                {
                    "id": "news-bing-management",
                    "name": "企业管理资讯",
                    "source_type": "news_search",
                    "config": {"engine": "bing_rss", "queries": queries},
                    "enabled": True,
                }
            )
        defaults.extend(
            [
                {
                    "id": "hot-weibo",
                    "name": "微博热榜",
                    "source_type": "hot_api",
                    "config": dict(_BUILTIN_HOT_SOURCES["hot-weibo"]),
                    "enabled": True,
                },
                {
                    "id": "hot-baidu",
                    "name": "百度热榜",
                    "source_type": "hot_api",
                    "config": dict(_BUILTIN_HOT_SOURCES["hot-baidu"]),
                    "enabled": True,
                },
            ]
        )
        custom_urls = list(topics.get("hot_api_urls") or [])
        legacy_url = str(topics.get("hot_api_url") or "").strip()
        if legacy_url:
            custom_urls.append({"name": "自定义热点源", "url": legacy_url})
        for index, raw in enumerate(custom_urls, 1):
            if isinstance(raw, str):
                raw = {"name": f"自定义热点源 {index}", "url": raw}
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            if not url:
                continue
            defaults.append(
                {
                    "id": "custom-hot-" + _short_hash(url),
                    "name": str(raw.get("name") or f"自定义热点源 {index}"),
                    "source_type": "hot_api",
                    "config": {
                        "url": url,
                        "query_param": str(raw.get("query_param") or ""),
                    },
                    "enabled": bool(raw.get("enabled", True)),
                }
            )
        for source in defaults:
            source_id = str(source["id"])
            existing = self.db.get_topic_source(source_id)
            if not existing:
                self.db.upsert_topic_source(source)
                continue
            # Migrate only the obsolete built-in third-party endpoints. A user
            # supplied URL on any other source is never overwritten.
            if source_id in _BUILTIN_HOT_SOURCES:
                current_url = str((existing.get("config") or {}).get("url") or "")
                if "api.vvhan.com/api/hotlist/" in current_url:
                    source["enabled"] = bool(existing.get("enabled", True))
                    self.db.upsert_topic_source(source)
        self._defaults_ensured_for.add(owner_key)

    def list_sources(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        self.ensure_defaults()
        return self.db.list_topic_sources(enabled_only=enabled_only)

    def save_source(self, source: dict[str, Any]) -> dict[str, Any]:
        name = str(source.get("name") or "").strip()
        source_type = str(source.get("source_type") or "").strip()
        if not name:
            raise ValueError("来源名称不能为空")
        if source_type not in SOURCE_TYPES:
            raise ValueError("不支持的选题来源类型")
        config = dict(source.get("config") or {})
        if source_type in {"rss", "hot_api"} and not str(config.get("url") or "").strip():
            raise ValueError("该来源必须填写 URL")
        if source_type == "news_search" and not list(config.get("queries") or []):
            raise ValueError("新闻搜索至少需要一个关键词")
        source_id = str(source.get("id") or uuid.uuid4().hex[:16])
        self.db.upsert_topic_source(
            {
                "id": source_id,
                "name": name,
                "source_type": source_type,
                "config": config,
                "enabled": bool(source.get("enabled", True)),
            }
        )
        return self.db.get_topic_source(source_id) or {}

    def delete_source(self, source_id: str) -> None:
        source = self.db.get_topic_source(source_id)
        if not source:
            return
        if str(source.get("source_type")) in {"manual", "followed_accounts"}:
            raise ValueError("系统内置来源不能删除，可以停用")
        self.db.delete_topic_source(source_id)

    def refresh(
        self,
        source_ids: list[str] | None = None,
        *,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        self.ensure_defaults()
        selected = set(source_ids or [])
        sources = [
            item
            for item in self.db.list_topic_sources(enabled_only=True)
            if not selected
            or str(item["id"]) in selected
            or str(item.get("source_key") or "") in selected
        ]
        report: list[dict[str, Any]] = []
        total = 0
        for source in sources:
            try:
                items = self._fetch_source(source, timeout=timeout)
                for item in items:
                    self.db.upsert_topic_item(self._normalize_item(source, item))
                self.db.update_topic_source_sync(str(source["id"]), error="")
                report.append(
                    {"source_id": source["id"], "name": source["name"], "count": len(items), "error": ""}
                )
                total += len(items)
            except Exception as exc:  # noqa: BLE001
                error = _friendly_error(exc)
                self.db.update_topic_source_sync(str(source["id"]), error=error)
                report.append(
                    {"source_id": source["id"], "name": source["name"], "count": 0, "error": error}
                )
        return {"total": total, "sources": report}

    def search(
        self,
        keyword: str,
        source_ids: list[str] | None = None,
        *,
        days: int = 7,
        timeout: float = 15.0,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Search one keyword across all selected source adapters.

        News search and keyword-aware custom APIs receive the keyword directly.
        RSS and ordinary hot-list APIs are fetched and filtered locally. Internal
        sources query their own persisted pools without network requests.
        """
        keyword = re.sub(r"\s+", " ", keyword or "").strip()
        if not keyword:
            raise ValueError("请输入要搜索的热点关键词")
        self.ensure_defaults()
        selected = set(source_ids or [])
        sources = [
            item
            for item in self.db.list_topic_sources(enabled_only=True)
            if not selected
            or str(item["id"]) in selected
            or str(item.get("source_key") or "") in selected
        ]
        if not sources:
            raise ValueError("请至少选择一个已启用的选题来源")
        owner_user_id = self.db.owner_user_id

        def search_one(
            source: dict[str, Any],
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            with customer_data_scope(owner_user_id):
                try:
                    items = self._search_source(
                        source,
                        keyword=keyword,
                        days=days,
                        timeout=timeout,
                    )
                    normalized_items: list[dict[str, Any]] = []
                    for item in items:
                        normalized = self._normalize_item(source, item)
                        self.db.upsert_topic_item(normalized)
                        normalized_items.append(
                            {
                                **normalized,
                                "source_name": str(source["name"]),
                                "source_type": str(source["source_type"]),
                                "favorite": 0,
                                "used": 0,
                            }
                        )
                    self.db.update_topic_source_sync(
                        str(source["id"]), error=""
                    )
                    return (
                        {
                            "source_id": source["id"],
                            "name": source["name"],
                            "count": len(normalized_items),
                            "error": "",
                        },
                        normalized_items,
                    )
                except Exception as exc:  # noqa: BLE001
                    error = _friendly_error(exc)
                    self.db.update_topic_source_sync(
                        str(source["id"]), error=error
                    )
                    return (
                        {
                            "source_id": source["id"],
                            "name": source["name"],
                            "count": 0,
                            "error": error,
                        },
                        [],
                    )
        report: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(6, len(sources))) as executor:
            futures = [executor.submit(search_one, source) for source in sources]
            for future in futures:
                source_report, source_items = future.result()
                report.append(source_report)
                results.extend(source_items)
        unique = _unique_items(results)
        unique.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
        return {
            "keyword": keyword,
            "total": len(unique[: max(1, limit)]),
            "sources": report,
            "items": unique[: max(1, limit)],
        }

    def list_topics(
        self,
        *,
        source_ids: list[str] | None = None,
        days: int = 7,
        keyword: str = "",
        favorite_only: bool = False,
        unused_only: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        self.ensure_defaults()
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))).isoformat()
        return self.db.list_topic_items(
            source_ids=source_ids,
            since=since,
            keyword=keyword,
            favorite_only=favorite_only,
            unused_only=unused_only,
            limit=limit,
        )

    def update_topic_state(
        self,
        item_id: str,
        *,
        favorite: bool | None = None,
        used: bool | None = None,
    ) -> dict[str, Any]:
        """Update an item's operator state and return the persisted record."""

        item_id = str(item_id or "").strip()
        if not item_id:
            raise ValueError("选题 ID 不能为空")
        if favorite is None and used is None:
            raise ValueError("至少需要更新收藏或使用状态中的一项")
        if self.db.get_topic_item(item_id) is None:
            raise KeyError("选题不存在")
        self.db.update_topic_item_flags(item_id, favorite=favorite, used=used)
        updated = self.db.get_topic_item(item_id)
        if updated is None:
            raise KeyError("选题不存在")
        return updated

    def add_manual_topic(
        self, title: str, *, url: str = "", summary: str = "", category: str = ""
    ) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise ValueError("选题标题不能为空")
        self.ensure_defaults()
        source = self.db.get_topic_source("internal-manual-topics")
        if not source:
            raise RuntimeError("手动选题来源初始化失败")
        item = self._normalize_item(
            source,
            {
                "title": title,
                "url": url.strip(),
                "summary": summary.strip(),
                "category": category.strip(),
                "published_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.db.upsert_topic_item(item)
        return item

    def _fetch_source(self, source: dict[str, Any], *, timeout: float) -> list[dict[str, Any]]:
        source_type = str(source.get("source_type") or "")
        config = dict(source.get("config") or {})
        if source_type == "manual":
            return []
        if source_type == "followed_accounts":
            return [
                {
                    "title": item["title"],
                    "url": item["url"],
                    "published_at": item.get("published_at") or item.get("discovered_at"),
                    "summary": item.get("summary") or "",
                    "raw": {"followed_article_id": item["id"], "account_name": item.get("account_name")},
                }
                for item in self.db.list_followed_articles(limit=500)
            ]
        with httpx.Client(
            timeout=timeout,
            headers=_BROWSER_HEADERS,
            follow_redirects=True,
            transport=httpx.HTTPTransport(retries=2),
        ) as client:
            if source_type == "rss":
                return _fetch_rss(client, str(config.get("url") or ""))
            if source_type == "news_search":
                result: list[dict[str, Any]] = []
                for query in config.get("queries") or []:
                    result.extend(
                        _fetch_bing_news(
                            client,
                            str(query),
                            focus_terms=self._news_focus_terms(),
                        )
                    )
                return _unique_items(result)
            if source_type == "hot_api":
                return _fetch_hot_api(client, config)
        raise ValueError(f"不支持的来源类型：{source_type}")

    def _search_source(
        self,
        source: dict[str, Any],
        *,
        keyword: str,
        days: int,
        timeout: float,
    ) -> list[dict[str, Any]]:
        source_type = str(source.get("source_type") or "")
        source_id = str(source["id"])
        since = (
            datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days), 365)))
        ).isoformat()
        if source_type == "manual":
            items = self.db.list_topic_items(
                source_ids=[source_id],
                since=since,
                limit=500,
            )
            return [item for item in items if _matches_keyword(item, keyword)]
        if source_type == "followed_accounts":
            items = [
                {
                    "title": item["title"],
                    "url": item["url"],
                    "published_at": item.get("published_at") or item.get("discovered_at"),
                    "summary": item.get("summary") or "",
                    "category": item.get("account_name") or "",
                    "raw": {
                        "followed_article_id": item["id"],
                        "account_name": item.get("account_name"),
                    },
                }
                for item in self.db.list_followed_articles(
                    since=since,
                    limit=500,
                )
            ]
            return [item for item in items if _matches_keyword(item, keyword)]

        config = dict(source.get("config") or {})
        with httpx.Client(
            timeout=timeout,
            headers=_BROWSER_HEADERS,
            follow_redirects=True,
            transport=httpx.HTTPTransport(retries=2),
        ) as client:
            if source_type == "news_search":
                return _fetch_bing_news(
                    client,
                    keyword,
                    focus_terms=self._news_focus_terms(),
                )
            if source_type == "rss":
                url, params, direct = _keyword_request(config, keyword)
                items = _fetch_rss(client, url, params=params)
                return items if direct else [item for item in items if _matches_keyword(item, keyword)]
            if source_type == "hot_api":
                url, params, direct = _keyword_request(config, keyword)
                request_config = {**config, "url": url, "params": params or None}
                items = _fetch_hot_api(client, request_config)
                return items if direct else [item for item in items if _matches_keyword(item, keyword)]
        raise ValueError(f"该来源不支持关键词搜索：{source_type}")

    def _news_focus_terms(self) -> tuple[str, ...]:
        configured = list((self.config.get("topics") or {}).get("focus_terms") or [])
        terms = [
            str(item).strip()
            for item in configured
            if str(item).strip()
        ]
        return tuple(terms or ("企业", "公司", "经营", "管理", "组织", "战略", "项目", "商业"))

    @staticmethod
    def _normalize_item(source: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
        url = str(item.get("url") or "").strip()
        if not title:
            raise ValueError("来源返回了空标题")
        source_id = str(source["id"])
        key = "|".join((source_id, title, url))
        return {
            "id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:24],
            "source_id": source_id,
            "title": title,
            "url": url,
            "published_at": item.get("published_at") or datetime.now(timezone.utc).isoformat(),
            "summary": str(item.get("summary") or ""),
            "category": str(item.get("category") or ""),
            "raw": dict(item.get("raw") or {}),
        }


def _fetch_rss(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    response = client.get(url, params=params or None)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    result: list[dict[str, Any]] = []
    for node in root.findall(".//item"):
        title = str(node.findtext("title") or "").strip()
        if not title:
            continue
        published = _parse_date(str(node.findtext("pubDate") or ""))
        result.append(
            {
                "title": title,
                "url": str(node.findtext("link") or "").strip(),
                "published_at": published.isoformat() if published else datetime.now(timezone.utc).isoformat(),
                "summary": _strip_html(str(node.findtext("description") or ""))[:300],
            }
        )
    return result


def _fetch_bing_news(
    client: httpx.Client,
    keyword: str,
    *,
    limit: int = 20,
    focus_terms: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Use Bing News RSS first and fall back when Bing returns an empty feed."""
    rss_url = (
        "https://www.bing.com/news/search?q="
        f"{quote_plus(keyword)}&format=rss&setlang=zh-cn"
    )
    rss_items = _fetch_rss(client, rss_url)
    if rss_items:
        return rss_items[:limit]

    search_query = keyword
    if focus_terms and not any(term.casefold() in keyword.casefold() for term in focus_terms):
        search_query += " 企业 管理"
    web_url = (
        "https://www.bing.com/search?q="
        f"{quote_plus(search_query + ' 最新新闻')}&setlang=zh-cn&count={max(10, limit)}"
    )
    response = client.get(web_url)
    response.raise_for_status()
    blocks = re.findall(
        r'<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>([\s\S]*?)</li>',
        response.text or "",
        flags=re.I,
    )
    observed_at = datetime.now(timezone.utc).isoformat()
    results: list[dict[str, Any]] = []
    for block in blocks:
        match = re.search(
            r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>',
            block,
            flags=re.I,
        )
        if not match:
            continue
        link = _unwrap_bing_url(html_module.unescape(match.group(1)))
        title = _strip_html(html_module.unescape(match.group(2)))
        snippet_match = re.search(r'<p[^>]*>([\s\S]*?)</p>', block, flags=re.I)
        summary = (
            _strip_html(html_module.unescape(snippet_match.group(1)))
            if snippet_match
            else ""
        )
        item = {
            "title": title,
            "url": link,
            "summary": summary[:300],
            "published_at": observed_at,
            "raw": {"discovery": "bing_web_fallback"},
        }
        focus_match = not focus_terms or any(
            term.casefold() in f"{title} {summary}".casefold()
            for term in focus_terms
        )
        if (
            title
            and link
            and _matches_keyword(item, keyword)
            and focus_match
            and _has_explicit_news_date(block)
        ):
            results.append(item)
        if len(results) >= limit:
            break
    return results


def _fetch_hot_api(
    client: httpx.Client,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fetch a hot list with the provider-specific request contract."""
    provider = str(config.get("provider") or "").strip().casefold()
    headers: dict[str, str] = {}
    if provider == "weibo":
        headers["Referer"] = "https://weibo.com/"
    elif provider == "baidu":
        headers["Referer"] = "https://top.baidu.com/board?tab=realtime"
    response = client.get(
        str(config.get("url") or ""),
        params=config.get("params"),
        headers=headers or None,
    )
    response.raise_for_status()
    return _parse_hot_payload(response.json(), provider=provider)


def _parse_hot_payload(data: Any, *, provider: str = "") -> list[dict[str, Any]]:
    if provider == "weibo":
        return _parse_weibo_hot_payload(data)
    if provider == "baidu":
        return _parse_baidu_hot_payload(data)
    if isinstance(data, dict):
        rows = data.get("data") or data.get("list") or data.get("items") or []
    elif isinstance(data, list):
        rows = data
    else:
        rows = []
    observed_at = datetime.now(timezone.utc).isoformat()
    result: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, str):
            title, url, summary = row, "", ""
        elif isinstance(row, dict):
            title = row.get("title") or row.get("name") or row.get("word") or row.get("query")
            url = row.get("url") or row.get("link") or row.get("mobilUrl") or ""
            summary = row.get("desc") or row.get("summary") or ""
        else:
            continue
        if str(title or "").strip():
            result.append(
                {"title": str(title).strip(), "url": str(url or ""), "summary": str(summary or ""), "published_at": observed_at}
            )
    return result


def _parse_weibo_hot_payload(data: Any) -> list[dict[str, Any]]:
    rows = (
        ((data.get("data") or {}).get("realtime") or [])
        if isinstance(data, dict)
        else []
    )
    observed_at = datetime.now(timezone.utc).isoformat()
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("word") or row.get("note") or "").strip()
        if not title or row.get("is_ad"):
            continue
        scheme = str(row.get("word_scheme") or title).strip()
        score = row.get("num")
        label = str(row.get("label_name") or row.get("icon_desc") or "").strip()
        summary_parts = []
        if score not in (None, ""):
            summary_parts.append(f"热度 {score}")
        if label:
            summary_parts.append(label)
        result.append(
            {
                "title": title,
                "url": f"https://s.weibo.com/weibo?q={quote_plus(scheme)}",
                "summary": " · ".join(summary_parts),
                "category": label,
                "published_at": observed_at,
                "raw": {
                    "rank": row.get("realpos") or row.get("rank"),
                    "score": score,
                    "provider": "weibo",
                },
            }
        )
    return result


def _parse_baidu_hot_payload(data: Any) -> list[dict[str, Any]]:
    cards = (
        ((data.get("data") or {}).get("cards") or [])
        if isinstance(data, dict)
        else []
    )
    observed_at = datetime.now(timezone.utc).isoformat()
    result: list[dict[str, Any]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        for group in card.get("content") or []:
            rows = group.get("content") if isinstance(group, dict) else []
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                title = str(row.get("word") or row.get("title") or "").strip()
                if not title:
                    continue
                label = str(row.get("newHotName") or row.get("labelTagName") or "").strip()
                index = row.get("index")
                summary = " · ".join(
                    value
                    for value in (
                        f"排名 {index}" if index not in (None, "") else "",
                        label,
                    )
                    if value
                )
                result.append(
                    {
                        "title": title,
                        "url": str(row.get("url") or ""),
                        "summary": summary,
                        "category": label,
                        "published_at": observed_at,
                        "raw": {
                            "rank": index,
                            "provider": "baidu",
                        },
                    }
                )
    return result


def _parse_date(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _unique_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (str(item.get("title") or ""), str(item.get("url") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _keyword_request(
    config: dict[str, Any], keyword: str
) -> tuple[str, dict[str, str], bool]:
    url = str(config.get("url") or "").strip()
    if "{keyword}" in url:
        return url.replace("{keyword}", quote_plus(keyword)), {}, True
    query_param = str(config.get("query_param") or "").strip()
    return url, ({query_param: keyword} if query_param else {}), bool(query_param)


def _unwrap_bing_url(url: str) -> str:
    if "bing.com/ck/a" not in url:
        return url
    encoded = (parse_qs(urlsplit(url).query).get("u") or [""])[0]
    if not encoded.startswith("a1"):
        return url
    payload = encoded[2:]
    try:
        padding = "=" * (-len(payload) % 4)
        return base64.urlsafe_b64decode(payload + padding).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return url


def _has_explicit_news_date(value: str) -> bool:
    text = _strip_html(html_module.unescape(value or ""))
    return bool(
        re.search(r"20\d{2}[-/年]\d{1,2}(?:[-/月]\d{1,2})?", text)
        or re.search(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+20\d{2}\b",
            text,
            flags=re.I,
        )
        or re.search(r"\d+\s*(?:分钟|小时|天)前", text)
    )


def _matches_keyword(item: dict[str, Any], keyword: str) -> bool:
    haystack = " ".join(
        str(item.get(key) or "") for key in ("title", "summary", "category")
    ).casefold()
    terms = [term.casefold() for term in re.split(r"\s+", keyword.strip()) if term]
    return bool(terms) and all(term in haystack for term in terms)


def _is_recent_search_item(item: dict[str, Any], days: int) -> bool:
    published = _parse_date(str(item.get("published_at") or ""))
    if published is None:
        return True
    now = datetime.now(timezone.utc)
    # “近 N 天”按自然日筛选；若使用滚动的 N×24 小时，同一日期的
    # 较早文章会在当天中途被意外排除，列表结果也会随刷新时刻变化。
    cutoff = (now - timedelta(days=max(1, min(int(days), 365)))).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return cutoff <= published <= now + timedelta(hours=6)


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "连接超时，请稍后重试"
    if isinstance(exc, (httpx.ConnectError, httpx.ReadError)):
        if "10054" in str(exc):
            return "来源服务器主动中断连接，已跳过该来源"
        return "无法连接该来源，请稍后重试"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"来源返回 HTTP {exc.response.status_code}"
    if isinstance(exc, json.JSONDecodeError):
        return "接口没有返回有效 JSON"
    return str(exc)[:300]
