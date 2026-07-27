from __future__ import annotations

import logging
import random
import re
from typing import Any

from app.wechat.client import WeChatClient
from app.wechat.draft import article_from_news_item, batchget_drafts, get_draft

logger = logging.getLogger(__name__)

# 标题中包含「广告」即纳入候选（广告1：/广告2:/广告：…）
_AD_TITLE_RE = re.compile(r"广告\s*(?:(\d+)\s*)?[：:]?\s*")
# 微信单条多图文最多 8 篇（含主稿），次条上限 7
_WECHAT_MAX_SECONDARY = 7


def strip_ad_title_prefix(title: str) -> str:
    """去掉标题中的「广告 / 广告X：」标记，正式上线不展示该标记。"""
    title = (title or "").strip()
    m = _AD_TITLE_RE.search(title)
    if not m:
        return title
    cleaned = (title[: m.start()] + title[m.end() :]).strip(" ：:")
    return cleaned if cleaned else title


def parse_ad_number(title: str) -> int | None:
    """有编号返回数字；仅「广告：」而无编号返回 0；非广告返回 None。"""
    title = (title or "").strip()
    m = _AD_TITLE_RE.search(title)
    if not m:
        return None
    if m.group(1) is None:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def is_ad_titled(title: str) -> bool:
    return "广告" in (title or "")


def list_flat_draft_articles(
    client: WeChatClient,
    *,
    max_drafts: int = 40,
) -> list[dict[str, Any]]:
    """展开草稿箱：每个 news_item 一条。"""
    out: list[dict[str, Any]] = []
    offset = 0
    page = 20
    scanned = 0
    while scanned < max_drafts:
        data = batchget_drafts(client, offset=offset, count=page, no_content=0)
        items = data.get("item") or []
        total = int(data.get("total_count") or 0)
        for item in items:
            scanned += 1
            media_id = str(item.get("media_id") or "")
            news_item = ((item.get("content") or {}).get("news_item")) or []
            for idx, ni in enumerate(news_item):
                art = article_from_news_item(ni)
                out.append(
                    {
                        "draft_media_id": media_id,
                        "index": idx,
                        "title": art.get("title") or "",
                        "article": art,
                        "package_size": len(news_item),
                        "update_time": int(item.get("update_time") or 0),
                    }
                )
            if scanned >= max_drafts:
                break
        offset += len(items)
        if not items or offset >= total:
            break
    return out


def select_secondary_articles(
    client: WeChatClient,
    layout_cfg: dict[str, Any] | None,
    *,
    exclude_titles: list[str] | None = None,
) -> list[dict[str, Any]]:
    """挑选多图文下方次条：标题含「广告」的草稿优先，默认稳定排序。"""
    cfg = layout_cfg or {}
    if not cfg.get("enabled", True):
        return []

    # 0 / 未填 = 搜到多少挂多少（仍受微信上限约束）
    raw_count = cfg.get("secondary_count")
    if raw_count is None or int(raw_count) <= 0:
        count = _WECHAT_MAX_SECONDARY
        take_all = True
    else:
        count = max(0, min(int(raw_count), _WECHAT_MAX_SECONDARY))
        take_all = False
    if count <= 0:
        return []

    skip_nums = {
        int(x)
        for x in (cfg.get("skip_ad_numbers") or [])
        if str(x).isdigit() or isinstance(x, int)
    }
    strip_prefix = bool(cfg.get("strip_ad_prefix", True))
    do_shuffle = bool(cfg.get("shuffle", True))

    exclude = {t.strip() for t in (exclude_titles or []) if t and str(t).strip()}
    picked: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    used_image_keys: set[str] = set()

    # 1) 固定 media_id（可带 index）— 仍先入选
    for raw in cfg.get("secondary_media_ids") or []:
        raw = str(raw or "").strip()
        if not raw:
            continue
        mid, idx = raw, 0
        if ":" in raw:
            mid, idx_s = raw.split(":", 1)
            mid = mid.strip()
            try:
                idx = int(idx_s)
            except ValueError:
                idx = 0
        key = f"{mid}#{idx}"
        if key in used_keys:
            continue
        article = _load_article_at(client, mid, idx)
        if not article or not article.get("thumb_media_id") or not article.get("content"):
            continue
        if article.get("title") in exclude:
            continue
        article = _finalize_secondary(article, mid, idx, strip_prefix=strip_prefix)
        image_key = _image_key(article)
        if image_key and image_key in used_image_keys:
            continue
        picked.append(article)
        used_keys.add(key)
        if image_key:
            used_image_keys.add(image_key)
        if not take_all and len(picked) >= count:
            return _maybe_shuffle(picked, do_shuffle)

    flats: list[dict[str, Any]] = []
    try:
        flats = list_flat_draft_articles(
            client, max_drafts=int(cfg.get("scan_limit") or 80)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("list flat drafts failed: %s", exc)

    # 2) 筛标题中包含「广告」的全部条目
    ad_rows: list[dict[str, Any]] = []
    keywords = [str(k).strip() for k in (cfg.get("title_keywords") or []) if str(k).strip()]
    for row in flats:
        title = row.get("title") or ""
        if title in exclude:
            continue
        num = parse_ad_number(title)
        if num is None:
            if keywords and any(kw in title for kw in keywords):
                num = 999
            else:
                continue
        if num in skip_nums:
            continue
        mid = row.get("draft_media_id") or ""
        idx = int(row.get("index") or 0)
        key = f"{mid}#{idx}"
        if not mid or key in used_keys:
            continue
        article = row.get("article")
        if not article or not article.get("thumb_media_id") or not article.get("content"):
            continue
        ad_rows.append(row)

    if do_shuffle:
        random.shuffle(ad_rows)
    else:
        ad_rows.sort(key=_ad_row_sort_key)

    for row in ad_rows:
        if len(picked) >= count:
            break
        mid = row.get("draft_media_id") or ""
        idx = int(row.get("index") or 0)
        key = f"{mid}#{idx}"
        if key in used_keys:
            continue
        num = parse_ad_number(row.get("title") or "")
        article = _finalize_secondary(
            dict(row.get("article") or {}),
            mid,
            idx,
            strip_prefix=strip_prefix,
        )
        image_key = _image_key(article)
        if image_key and image_key in used_image_keys:
            continue
        if num is not None:
            article["_ad_number"] = num
        picked.append(article)
        used_keys.add(key)
        if image_key:
            used_image_keys.add(image_key)

    if take_all and len(ad_rows) > count:
        logger.warning(
            "ad secondaries truncated: found=%s kept=%s (WeChat max %s secondaries)",
            len(ad_rows),
            count,
            _WECHAT_MAX_SECONDARY,
        )

    # 3) 不够且允许 fallback：最近单图文（非广告）
    if cfg.get("fallback_recent", False) and len(picked) < count:
        for row in flats:
            if len(picked) >= count:
                break
            if int(row.get("package_size") or 1) != 1 and cfg.get("prefer_single_only", True):
                continue
            title = row.get("title") or ""
            if not title or title in exclude or is_ad_titled(title):
                continue
            mid = row.get("draft_media_id") or ""
            idx = int(row.get("index") or 0)
            key = f"{mid}#{idx}"
            if not mid or key in used_keys:
                continue
            article = row.get("article")
            if not article or not article.get("thumb_media_id") or not article.get("content"):
                continue
            article = _finalize_secondary(dict(article), mid, idx, strip_prefix=False)
            image_key = _image_key(article)
            if image_key and image_key in used_image_keys:
                continue
            picked.append(article)
            used_keys.add(key)
            if image_key:
                used_image_keys.add(image_key)

    return _maybe_shuffle(picked[:count], do_shuffle)


def _maybe_shuffle(items: list[dict[str, Any]], do_shuffle: bool) -> list[dict[str, Any]]:
    if do_shuffle and len(items) > 1:
        random.shuffle(items)
    return items


def _image_key(article: dict[str, Any]) -> str:
    url = str(article.get("thumb_url") or "").strip().lower()
    if url:
        return url.split("?", 1)[0].replace("http://", "https://", 1)
    return str(article.get("thumb_media_id") or "").strip()


def _ad_row_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    number = parse_ad_number(str(row.get("title") or ""))
    return (
        number if number is not None else 10_000,
        -int(row.get("update_time") or 0),
        str(row.get("title") or ""),
    )


def _finalize_secondary(
    article: dict[str, Any],
    mid: str,
    idx: int,
    *,
    strip_prefix: bool,
) -> dict[str, Any]:
    out = dict(article)
    raw_title = str(out.get("title") or "")
    out["_raw_title"] = raw_title
    if strip_prefix:
        out["title"] = strip_ad_title_prefix(raw_title)[:64]
    out["_from_media_id"] = mid
    out["_from_index"] = idx
    return out


def _load_article_at(client: WeChatClient, media_id: str, index: int = 0) -> dict[str, Any] | None:
    try:
        data = get_draft(client, media_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_draft failed %s: %s", media_id[:16], exc)
        return None
    news_item = data.get("news_item") or []
    if index < 0 or index >= len(news_item):
        return None
    return article_from_news_item(news_item[index])


def compose_articles(
    main: dict[str, Any],
    secondaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """主条 + 次条，供 draft/add。次条标题已去「广告X：」前缀。"""
    articles = [main]
    for sec in secondaries:
        title = strip_ad_title_prefix(str(sec.get("title") or "未命名"))
        clean = {
            "title": title[:64],
            "author": sec.get("author") or "",
            "digest": "",
            "content": sec.get("content") or "",
            "content_source_url": sec.get("content_source_url") or "",
            "thumb_media_id": sec.get("thumb_media_id") or "",
            "need_open_comment": int(sec.get("need_open_comment") or 0),
            "only_fans_can_comment": int(sec.get("only_fans_can_comment") or 0),
        }
        if not clean["thumb_media_id"] or not clean["content"]:
            continue
        articles.append(clean)
    return articles
