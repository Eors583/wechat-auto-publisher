from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db import Database


def sync_ads_from_config(db: Database, ads_cfg: dict[str, Any]) -> None:
    pool = ads_cfg.get("default_pool") or []
    for item in pool:
        if isinstance(item, str):
            db.upsert_ad({"id": item, "title": item, "enabled": True, "priority": 0})
        elif isinstance(item, dict) and item.get("id"):
            db.upsert_ad(item)


def select_ad(db: Database, ads_cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if ads_cfg:
        sync_ads_from_config(db, ads_cfg)
    ads = db.list_ads(enabled_only=True)
    now = datetime.now(timezone.utc)

    usable: list[dict[str, Any]] = []
    for ad in ads:
        expires_at = ad.get("expires_at")
        if expires_at and _parse_dt(expires_at) and _parse_dt(expires_at) < now:
            continue
        usable.append(ad)

    if not usable:
        return None

    def sort_key(ad: dict[str, Any]) -> tuple:
        course = _parse_dt(ad.get("course_start_at"))
        # Future courses first, sooner first; missing course_start last
        if course and course >= now:
            course_rank = 0
            course_ts = course.timestamp()
        else:
            course_rank = 1
            course_ts = float("inf")
        priority = -int(ad.get("priority") or 0)  # higher priority first
        last_used = _parse_dt(ad.get("last_used_at"))
        last_ts = last_used.timestamp() if last_used else 0.0
        return (course_rank, course_ts, priority, last_ts)

    usable.sort(key=sort_key)
    chosen = usable[0]
    db.mark_ad_used(chosen["id"])
    return chosen


def render_ad_html(ad: dict[str, Any] | None) -> str:
    if not ad:
        return ""
    title = ad.get("title") or "相关推荐"
    desc = ad.get("description") or ""
    url = ad.get("url") or ""
    parts = [
        f'<p style="margin:0 0 8px 0;font-size:15px;color:#1a1a1a;font-weight:650;">{title}</p>',
    ]
    if desc:
        parts.append(
            f'<p style="margin:0 0 10px 0;font-size:14px;color:#666;line-height:1.7;">{desc}</p>'
        )
    if url:
        parts.append(
            f'<p style="margin:0;font-size:14px;"><a href="{url}" style="color:#576b95;text-decoration:none;">查看详情</a></p>'
        )
    return "\n".join(parts)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
