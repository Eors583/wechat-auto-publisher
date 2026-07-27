from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx

from .topic import Topic

logger = logging.getLogger(__name__)

_DEFAULT_FOCUS_KEYWORDS = ("企业", "管理", "项目", "组织")
_DEFAULT_FOCUS_TERMS = (
    "企业", "公司", "经营", "业务", "商业",
    "管理", "战略", "流程", "效率", "绩效", "领导力",
    "项目", "交付", "PMO", "协同",
    "组织", "架构", "机制", "团队", "人才",
)
_DEFAULT_NEWS_QUERIES = (
    "企业 经营 管理",
    "企业 项目 管理",
    "企业 组织 管理",
    "企业 战略 管理",
)


def _data_dir(config: dict[str, Any]) -> Path:
    return Path(config.get("_data_dir") or Path(config["_root"]) / "data")


def load_keywords(config: dict[str, Any]) -> list[str]:
    root = Path(config["_root"])
    topics_cfg = config.get("topics") or {}
    rel = topics_cfg.get("keywords_file") or "data/keywords.txt"
    path = root / rel
    if not path.exists():
        path = root / "examples" / "keywords.txt"
    if not path.exists():
        return []
    lines = [
        ln.strip()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    return lines


def load_peer_topics(config: dict[str, Any]) -> list[dict[str, Any]]:
    """返回 [{account, topic, url?}, ...]"""
    topics_cfg = config.get("topics") or {}
    peers = topics_cfg.get("peers") or []
    items: list[dict[str, Any]] = []
    for peer in peers:
        if not isinstance(peer, dict):
            continue
        name = str(peer.get("name") or peer.get("account") or "同行").strip()
        for t in peer.get("topics") or []:
            if isinstance(t, str) and t.strip():
                items.append({"account": name, "topic": t.strip(), "url": None})
            elif isinstance(t, dict) and t.get("topic"):
                items.append(
                    {
                        "account": name,
                        "topic": str(t["topic"]).strip(),
                        "url": t.get("url"),
                        "published_at": t.get("published_at"),
                    }
                )
    # 合并本地可编辑库
    cache = _data_dir(config) / "peer_topics.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            for row in data if isinstance(data, list) else []:
                if isinstance(row, dict) and row.get("topic"):
                    items.append(
                        {
                            "account": str(row.get("account") or "同行"),
                            "topic": str(row["topic"]).strip(),
                            "url": row.get("url"),
                            "published_at": row.get("published_at"),
                        }
                    )
        except json.JSONDecodeError:
            logger.warning("Invalid peer_topics.json")
    # 只保留企业、管理、项目、组织方向的同行内容，再去重。
    items = [it for it in items if _is_focus_topic(str(it.get("topic") or ""), config)]
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for it in items:
        key = f"{it['account']}|{it['topic']}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)
    return unique


def load_cached_hot(config: dict[str, Any]) -> list[dict[str, Any]]:
    path = _data_dir(config) / "hot_topics.json"
    if not path.exists():
        return _fallback_hot()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else data
        out = []
        for row in items or []:
            if isinstance(row, str) and row.strip():
                # 旧缓存没有日期，不能证明属于近一周，直接丢弃。
                continue
            elif isinstance(row, dict) and row.get("title"):
                item = {
                    "title": str(row["title"]).strip(),
                    "source": str(row.get("source") or "cache"),
                    "url": str(row.get("url") or ""),
                    "published_at": str(row.get("published_at") or ""),
                }
                if _is_recent_item(item, config) and _is_focus_topic(item["title"], config):
                    out.append(item)
        return out or _fallback_hot(config)
    except Exception:  # noqa: BLE001
        return _fallback_hot(config)


def _fallback_hot(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    today = datetime.now(timezone.utc).date().isoformat()
    return [
        {"title": "企业数字化转型中最容易失控的管理环节", "source": "fallback", "published_at": today, "url": ""},
        {"title": "企业项目管理如何避免目标与执行脱节", "source": "fallback", "published_at": today, "url": ""},
        {"title": "组织变革为什么常常卡在中层管理者", "source": "fallback", "published_at": today, "url": ""},
        {"title": "企业如何建立跨部门项目协同机制", "source": "fallback", "published_at": today, "url": ""},
        {"title": "管理者如何提升组织执行力", "source": "fallback", "published_at": today, "url": ""},
    ]


def fetch_hot_topics(config: dict[str, Any], *, timeout: float = 12.0) -> list[dict[str, Any]]:
    """拉取近 7 天企业/管理/项目/组织资讯；当日综合热榜仅作补充。"""
    topics_cfg = config.get("topics") or {}
    custom_url = (topics_cfg.get("hot_api_url") or "").strip()
    candidates: list[tuple[str, str]] = []
    if custom_url:
        candidates.append(("custom", custom_url))
    # 若干公开热榜聚合接口（不稳定时自动降级）
    candidates.extend(
        [
            ("vvhan-wb", "https://api.vvhan.com/api/hotlist/wbHot"),
            ("vvhan-baidu", "https://api.vvhan.com/api/hotlist/baiduRD"),
        ]
    )

    headers = {"User-Agent": "Mozilla/5.0 WeChatAutoPublisher/0.1"}
    collected: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        collected.extend(_fetch_recent_news_rss(client, config))
        for source, url in candidates:
            try:
                resp = client.get(url)
                if resp.status_code >= 400:
                    continue
                data = resp.json()
                items = _parse_hot_payload(data, source)
                if items:
                    observed_at = datetime.now(timezone.utc).isoformat()
                    collected.extend(
                        {
                            **item,
                            "published_at": observed_at,
                            "url": str(item.get("url") or ""),
                        }
                        for item in items
                        if _is_focus_topic(str(item.get("title") or ""), config)
                    )
                    break
            except Exception as exc:  # noqa: BLE001
                logger.info("Hot fetch failed %s: %s", source, exc)

    if not collected:
        return load_cached_hot(config)

    # 去重截断
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for it in collected:
        title = it["title"].strip()
        if (
            not title
            or title in seen
            or not _is_focus_topic(title, config)
            or not _is_recent_item(it, config)
        ):
            continue
        seen.add(title)
        unique.append(
            {
                "title": title,
                "source": it.get("source") or "hot",
                "url": str(it.get("url") or ""),
                "published_at": str(it.get("published_at") or ""),
            }
        )
        if len(unique) >= 30:
            break

    cache_path = _data_dir(config) / "hot_topics.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {"fetched_at": datetime.now(timezone.utc).isoformat(), "items": unique},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return unique


def _parse_hot_payload(data: Any, source: str) -> list[dict[str, Any]]:
    rows: list[Any]
    if isinstance(data, dict):
        rows = data.get("data") or data.get("list") or data.get("items") or []
    elif isinstance(data, list):
        rows = data
    else:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        title = None
        if isinstance(row, str):
            title = row
        elif isinstance(row, dict):
            title = row.get("title") or row.get("name") or row.get("word") or row.get("query")
        if title and str(title).strip():
            out.append({"title": str(title).strip(), "source": source})
    return out


def _fetch_recent_news_rss(
    client: httpx.Client,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    topics_cfg = config.get("topics") or {}
    queries = topics_cfg.get("news_queries") or list(_DEFAULT_NEWS_QUERIES)
    out: list[dict[str, Any]] = []
    rss_sources = topics_cfg.get("rss_sources") or [
        {"name": "36氪", "url": "https://36kr.com/feed"}
    ]
    for raw_source in rss_sources:
        if not isinstance(raw_source, dict):
            continue
        source_name = str(raw_source.get("name") or "行业资讯").strip()
        source_url = str(raw_source.get("url") or "").strip()
        if not source_url:
            continue
        try:
            response = client.get(source_url)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            out.extend(_rss_items(root, source_name, config))
        except Exception as exc:  # noqa: BLE001
            logger.info("Industry RSS failed %s: %s", source_name, exc)

    for raw_query in queries:
        query = str(raw_query or "").strip()
        if not query:
            continue
        url = (
            "https://www.bing.com/news/search?q="
            f"{quote_plus(query)}&format=rss&setlang=zh-cn"
        )
        try:
            response = client.get(url)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception as exc:  # noqa: BLE001
            logger.info("Recent news RSS failed %s: %s", query, exc)
            continue
        out.extend(_rss_items(root, "近7天企业管理资讯", config))
    out.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)
    unique: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for item in out:
        title = str(item.get("title") or "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        unique.append(item)
    return unique


def _rss_items(
    root: ET.Element,
    source: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in root.findall(".//item"):
        title = str(node.findtext("title") or "").strip()
        link = str(node.findtext("link") or "").strip()
        published_at = _parse_date(str(node.findtext("pubDate") or ""))
        item = {
            "title": title,
            "source": source,
            "url": link,
            "published_at": published_at.isoformat() if published_at else "",
        }
        if title and _is_focus_topic(title, config) and _is_recent_item(item, config):
            out.append(item)
    return out


def _focus_keywords(config: dict[str, Any] | None) -> tuple[str, ...]:
    topics_cfg = (config or {}).get("topics") or {}
    values = topics_cfg.get("focus_terms") or _DEFAULT_FOCUS_TERMS
    return tuple(str(x).strip() for x in values if str(x).strip())


def _is_focus_topic(text: str, config: dict[str, Any] | None = None) -> bool:
    value = (text or "").strip()
    return bool(value) and any(keyword in value for keyword in _focus_keywords(config))


def _recent_days(config: dict[str, Any] | None) -> int:
    topics_cfg = (config or {}).get("topics") or {}
    return max(1, min(int(topics_cfg.get("recent_days") or 7), 30))


def _is_recent_item(item: dict[str, Any], config: dict[str, Any] | None = None) -> bool:
    published = _parse_date(str(item.get("published_at") or ""))
    if published is None:
        return False
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=_recent_days(config))
    return cutoff <= published <= now + timedelta(hours=6)


def _parse_date(value: str) -> datetime | None:
    text = re.sub(r"\s+", " ", (value or "").strip())
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


def topic_from_choice(
    *,
    source: str,
    text: str,
    meta: dict[str, Any] | None = None,
) -> Topic:
    return Topic(topic=text.strip(), source=source, meta=meta or {})
