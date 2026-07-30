from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx
from PIL import Image

from app.db import Database
from app.wechat.client import WeChatClient
from app.wechat.factory import build_wechat_client

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkArticle:
    title: str
    cover_url: str = ""
    url: str = ""


@dataclass
class BenchmarkRecord:
    published_at: int
    articles: list[BenchmarkArticle]
    source: str


def fetch_latest_benchmark_record(
    config: dict[str, Any],
    db: Database,
) -> BenchmarkRecord | None:
    """取蓝血研究的最新发表组；后台记录优先，开放接口只作新鲜数据回退。"""
    cfg = config.get("benchmark") or {}
    if not cfg.get("enabled", False):
        return None

    cache_path = _cache_path(config, cfg)
    admin_cookie = str(cfg.get("admin_cookie") or "").strip()
    admin_token = _admin_token(str(cfg.get("admin_token") or cfg.get("admin_url") or ""))
    if admin_cookie and admin_token:
        try:
            record = fetch_admin_publish_record(admin_cookie, admin_token)
            _save_cache(cache_path, record)
            return record
        except Exception as exc:  # noqa: BLE001
            logger.warning("benchmark admin publish fetch failed: %s", exc)

    # 当天已经核验过的记录直接使用，避免每次预览都等待开放接口返回旧数据。
    cached = _load_cache(cache_path)
    if cached and datetime.fromtimestamp(cached.published_at).date() == datetime.now().date():
        return cached
    if cached and not bool(cfg.get("official_fallback_enabled", False)):
        return cached

    app_id = str(cfg.get("app_id") or "").strip()
    app_secret = str(cfg.get("app_secret") or "").strip()
    if app_id and app_secret:
        try:
            client = build_wechat_client(
                config,
                db,
                app_id,
                app_secret,
            )
            record = fetch_official_publish_record(client)
            max_age = int(cfg.get("official_max_age_hours") or 36)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age)
            if record and datetime.fromtimestamp(record.published_at, timezone.utc) >= cutoff:
                _save_cache(cache_path, record)
                return record
        except Exception as exc:  # noqa: BLE001
            logger.warning("benchmark official publish fetch failed: %s", exc)

    return cached or _load_cache(cache_path)


def fetch_admin_publish_record(cookie: str, token: str) -> BenchmarkRecord:
    """读取公众号后台“发表记录”。Cookie 只在内存中使用，不写日志。"""
    params = {
        "action": "list",
        "sub": "list",
        "begin": 0,
        "count": 10,
        "token": token,
        "lang": "zh_CN",
        "f": "json",
        "ajax": 1,
    }
    headers = {
        "Cookie": cookie,
        "Referer": f"https://mp.weixin.qq.com/cgi-bin/appmsgpublish?sub=list&begin=0&count=10&token={token}&lang=zh_CN",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    }
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        response = client.get(
            "https://mp.weixin.qq.com/cgi-bin/appmsgpublish",
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        if "text/html" in response.headers.get("content-type", ""):
            raise RuntimeError("公众号后台登录态已失效")
        payload = response.json()
    record = parse_admin_publish_response(payload)
    if not record:
        raise RuntimeError("发表记录接口没有返回可识别的文章组")
    return record


def parse_admin_publish_response(payload: dict[str, Any]) -> BenchmarkRecord | None:
    """兼容后台接口多层 JSON 字符串及不同年份的字段名。"""
    root = _decode_jsonish(payload)
    page = _decode_jsonish(root.get("publish_page", root)) if isinstance(root, dict) else root
    publish_list = _first_list(page, ("publish_list", "list"))
    candidates: list[BenchmarkRecord] = []
    for raw in publish_list:
        info = _decode_jsonish(raw)
        if isinstance(info, dict) and "publish_info" in info:
            info = _decode_jsonish(info.get("publish_info"))
        if not isinstance(info, dict):
            continue
        appmsg = _decode_jsonish(
            info.get("appmsg_info")
            or info.get("appmsg_data")
            or info.get("content")
            or info
        )
        items = _find_article_items(appmsg)
        articles = [_article_from_mapping(x) for x in items if isinstance(x, dict)]
        articles = [x for x in articles if x.title]
        if not articles:
            continue
        published_at = _find_timestamp(info) or _find_timestamp(appmsg)
        candidates.append(BenchmarkRecord(published_at, articles, "admin_publish_record"))
    if not candidates:
        return None
    return max(candidates, key=lambda x: x.published_at)


def fetch_official_publish_record(client: WeChatClient) -> BenchmarkRecord | None:
    data = client.request(
        "POST",
        "/cgi-bin/freepublish/batchget",
        json_body={"offset": 0, "count": 20, "no_content": 1},
    )
    records: list[BenchmarkRecord] = []
    for item in data.get("item") or []:
        content = item.get("content") or {}
        articles = [_article_from_mapping(x) for x in content.get("news_item") or []]
        articles = [x for x in articles if x.title]
        if articles:
            records.append(
                BenchmarkRecord(
                    int(item.get("update_time") or content.get("update_time") or 0),
                    articles,
                    "official_freepublish",
                )
            )
    return max(records, key=lambda x: x.published_at) if records else None


def sync_secondary_titles(
    secondaries: list[dict[str, Any]],
    record: BenchmarkRecord | None,
    *,
    threshold: float = 0.90,
    matched_only: bool = False,
    follow_source_order: bool = True,
    deduplicate_by_image: bool = True,
) -> list[dict[str, Any]]:
    """按图片匹配标题；匹配项跟随对标顺序，未匹配项稳定追加。"""
    if not record or len(record.articles) < 2 or not secondaries:
        return secondaries
    sources = [a for a in record.articles[1:] if a.title and a.cover_url]
    targets = [dict(x) for x in secondaries]
    if not sources:
        return secondaries

    source_hashes = _download_hashes(a.cover_url for a in sources)
    target_hashes = _download_hashes(str(x.get("thumb_url") or "") for x in targets)
    candidate_targets: list[int] = []
    seen_hashes: set[int] = set()
    for target_idx, target_hash in enumerate(target_hashes):
        if deduplicate_by_image and target_hash is not None:
            if target_hash in seen_hashes:
                continue
            seen_hashes.add(target_hash)
        candidate_targets.append(target_idx)

    choices: list[tuple[float, int, int]] = []
    for source_idx, source_hash in enumerate(source_hashes):
        if source_hash is None:
            continue
        for target_idx in candidate_targets:
            target_hash = target_hashes[target_idx]
            if target_hash is None:
                continue
            choices.append((_hash_similarity(source_hash, target_hash), source_idx, target_idx))
    choices.sort(reverse=True)

    used_sources: set[int] = set()
    used_targets: set[int] = set()
    source_order_by_target: dict[int, int] = {}
    for score, source_idx, target_idx in choices:
        if score < threshold or source_idx in used_sources or target_idx in used_targets:
            continue
        target = targets[target_idx]
        target["_original_title"] = target.get("title") or ""
        target["title"] = sources[source_idx].title[:64]
        target["_benchmark_title"] = sources[source_idx].title
        target["_benchmark_image_score"] = round(score, 4)
        target["_benchmark_source"] = record.source
        target["_benchmark_published_at"] = record.published_at
        target["_benchmark_order"] = source_idx
        used_sources.add(source_idx)
        used_targets.add(target_idx)
        source_order_by_target[target_idx] = source_idx

    if matched_only:
        indices = list(used_targets)
    else:
        indices = candidate_targets

    if follow_source_order:
        matched = sorted(
            (idx for idx in indices if idx in used_targets),
            key=lambda idx: source_order_by_target[idx],
        )
        unmatched = sorted(
            (idx for idx in indices if idx not in used_targets),
            key=lambda idx: (_ad_sort_number(targets[idx]), idx),
        )
        indices = matched + unmatched
    else:
        indices.sort()
    return [targets[idx] for idx in indices]


def _ad_sort_number(article: dict[str, Any]) -> int:
    raw = article.get("_ad_number")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 10_000


def _download_hashes(urls: Iterable[str]) -> list[int | None]:
    result: list[int | None] = []
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        for url in urls:
            if not url:
                result.append(None)
                continue
            try:
                response = client.get(url)
                response.raise_for_status()
                result.append(_difference_hash(response.content))
            except Exception as exc:  # noqa: BLE001
                logger.warning("benchmark image download failed: %s", exc)
                result.append(None)
    return result


def _difference_hash(content: bytes) -> int:
    with Image.open(io.BytesIO(content)) as image:
        gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(gray.getdata())
    value = 0
    for row in range(8):
        for col in range(8):
            value = (value << 1) | int(pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    return value


def _hash_similarity(left: int, right: int) -> float:
    return 1.0 - ((left ^ right).bit_count() / 64.0)


def _article_from_mapping(item: dict[str, Any]) -> BenchmarkArticle:
    return BenchmarkArticle(
        title=str(item.get("title") or item.get("name") or "").strip(),
        cover_url=str(
            item.get("cover_url")
            or item.get("cover")
            or item.get("thumb_url")
            or item.get("cdn_url")
            or ""
        ).strip(),
        url=str(item.get("url") or item.get("content_url") or item.get("link") or "").strip(),
    )


def _decode_jsonish(value: Any) -> Any:
    current = value
    for _ in range(4):
        if not isinstance(current, str):
            break
        text = current.strip()
        if not text or text[0] not in "[{\"":
            break
        try:
            current = json.loads(text)
        except json.JSONDecodeError:
            break
    if isinstance(current, dict):
        return {k: _decode_jsonish(v) for k, v in current.items()}
    if isinstance(current, list):
        return [_decode_jsonish(v) for v in current]
    return current


def _first_list(value: Any, keys: tuple[str, ...]) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in keys:
            found = value.get(key)
            if isinstance(found, list):
                return found
    return []


def _find_article_items(value: Any) -> list[dict[str, Any]]:
    keys = ("item_list", "appmsg_item_list", "news_item", "articles", "items", "item")
    if isinstance(value, dict):
        for key in keys:
            found = value.get(key)
            if isinstance(found, list) and any(isinstance(x, dict) and x.get("title") for x in found):
                return found
        for nested in value.values():
            found = _find_article_items(nested)
            if found:
                return found
    elif isinstance(value, list):
        if any(isinstance(x, dict) and x.get("title") for x in value):
            return value
        for nested in value:
            found = _find_article_items(nested)
            if found:
                return found
    return []


def _find_timestamp(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    for key in ("send_time", "publish_time", "published_at", "update_time", "create_time"):
        raw = value.get(key)
        try:
            timestamp = int(raw or 0)
        except (TypeError, ValueError):
            continue
        if timestamp > 1_000_000_000:
            return timestamp
    return 0


def _admin_token(value: str) -> str:
    match = re.search(r"(?:^|[?&])token=(\d+)", value)
    return match.group(1) if match else value.strip()


def _cache_path(config: dict[str, Any], cfg: dict[str, Any]) -> Path:
    raw = str(cfg.get("cache_path") or "data/benchmark_latest.json")
    path = Path(raw)
    if not path.is_absolute():
        path = Path(str(config.get("_root") or ".")) / path
    return path


def _save_cache(path: Path, record: BenchmarkRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")


def _load_cache(path: Path) -> BenchmarkRecord | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return BenchmarkRecord(
            published_at=int(data.get("published_at") or 0),
            source=str(data.get("source") or "cache"),
            articles=[BenchmarkArticle(**x) for x in data.get("articles") or []],
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
