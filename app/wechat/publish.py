from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler

from .client import WeChatClient
from .draft import add_draft, build_article

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def submit_publish(client: WeChatClient, media_id: str) -> str:
    data = client.request(
        "POST",
        "/cgi-bin/freepublish/submit",
        json_body={"media_id": media_id},
    )
    publish_id = data.get("publish_id")
    if publish_id is None:
        raise RuntimeError(f"freepublish/submit missing publish_id: {data}")
    return str(publish_id)


def get_publish_status(client: WeChatClient, publish_id: str) -> dict[str, Any]:
    return client.request(
        "POST",
        "/cgi-bin/freepublish/get",
        json_body={"publish_id": publish_id},
    )


def ensure_draft_then_publish(
    client: WeChatClient,
    *,
    media_id: str | None,
    article: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Return (draft_media_id, publish_id). On publish failure, draft is preserved."""
    draft_id = media_id
    if not draft_id:
        if not article:
            raise ValueError("Either media_id or article is required")
        draft_id = add_draft(client, [article])
    try:
        publish_id = submit_publish(client, draft_id)
        return draft_id, publish_id
    except Exception:
        logger.exception("Publish failed; draft kept media_id=%s", draft_id)
        raise


def schedule_publish(
    run_at: datetime,
    job_id: int,
    callback: Callable[[int], None],
) -> None:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
        _scheduler.start()
    trigger_id = f"publish-job-{job_id}-{int(run_at.timestamp())}"
    _scheduler.add_job(
        callback,
        trigger="date",
        run_date=run_at,
        id=trigger_id,
        replace_existing=True,
        args=[job_id],
    )
    logger.info("Scheduled publish for job %s at %s", job_id, run_at.isoformat())


def build_article_from_job(
    job: dict[str, Any],
    *,
    author: str = "",
    need_open_comment: int = 0,
    only_fans_can_comment: int = 0,
) -> dict[str, Any]:
    return build_article(
        title=job.get("selected_title") or (job.get("titles") or ["未命名"])[0],
        content=job.get("html_content") or "",
        thumb_media_id=job.get("thumb_media_id") or "",
        author=author,
        digest=job.get("digest") or "",
        # The URL is only an internal ingestion source. Do not expose it in the
        # WeChat editor's “原文链接” field unless a future explicit feature asks for it.
        content_source_url="",
        need_open_comment=need_open_comment,
        only_fans_can_comment=only_fans_can_comment,
    )
