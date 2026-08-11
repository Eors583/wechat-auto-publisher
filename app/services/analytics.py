from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.db import JOB_STATUSES, Database
from app.time_utils import business_date, business_day_bounds_utc

_PROCESSING_STATUSES = {
    "pending",
    "ingesting",
    "rewriting",
    "title_optimizing",
    "rendering",
    "injecting",
}


class AnalyticsService:
    """Read-only operational statistics shared by every application surface.

    Article counts intentionally use ``batch_jobs`` rather than every legacy row
    in ``jobs``.  This matches the data overview and prevents an old standalone
    CLI job from being presented as part of a multi-account publishing batch.
    Archived batches remain part of historical totals, matching the existing
    desktop data page.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def get_overview(self, *, today: date | str | None = None) -> dict[str, Any]:
        """Return a detached, JSON-serializable operational overview."""

        today_text = _date_text(today)
        day_start, day_end = business_day_bounds_utc(
            date.fromisoformat(today_text)
        )
        day_start_text = day_start.isoformat(timespec="microseconds")
        day_end_text = day_end.isoformat(timespec="microseconds")
        owner_id = str(self.db.owner_user_id or "").strip()
        batch_owner_clause = "WHERE b.owner_user_id = ?" if owner_id else ""
        joined_owner_clause = "WHERE b.owner_user_id = ?" if owner_id else ""
        owner_params: tuple[str, ...] = (owner_id,) if owner_id else ()
        with self.db.connect() as conn:
            batch_row = conn.execute(
                f"""
                SELECT COUNT(*) AS total_batches,
                       SUM(CASE
                           WHEN b.created_at >= ? AND b.created_at < ? THEN 1
                           ELSE 0
                       END)
                           AS today_batches,
                       SUM(CASE WHEN b.archived_at IS NOT NULL THEN 1 ELSE 0 END)
                           AS archived_batches
                FROM batches AS b
                {batch_owner_clause}
                """,
                (day_start_text, day_end_text, *owner_params),
            ).fetchone()
            status_rows = conn.execute(
                f"""
                SELECT j.status, COUNT(*) AS article_count
                FROM batch_jobs AS bj
                JOIN jobs AS j ON j.id = bj.job_id
                JOIN batches AS b ON b.id = bj.batch_id
                {joined_owner_clause}
                GROUP BY j.status
                """,
                owner_params,
            ).fetchall()
            article_row = conn.execute(
                f"""
                SELECT COUNT(*) AS total_articles,
                       SUM(CASE
                           WHEN j.created_at >= ? AND j.created_at < ? THEN 1
                           ELSE 0
                       END)
                           AS today_articles
                FROM batch_jobs AS bj
                JOIN jobs AS j ON j.id = bj.job_id
                JOIN batches AS b ON b.id = bj.batch_id
                {joined_owner_clause}
                """,
                (day_start_text, day_end_text, *owner_params),
            ).fetchone()
            review_rows = conn.execute(
                f"""
                SELECT bj.review_status, COUNT(*) AS article_count
                FROM batch_jobs AS bj
                JOIN jobs AS j ON j.id = bj.job_id
                JOIN batches AS b ON b.id = bj.batch_id
                {joined_owner_clause}
                GROUP BY bj.review_status
                """,
                owner_params,
            ).fetchall()

        # Operational cards must use the same owner-aware, non-archived article
        # inbox as the task queue. A raw ``ready_for_review`` status count also
        # includes articles that an operator has already confirmed, which made
        # the top bar claim there was work that the queue could not display.
        inbox_counts = self.db.review_inbox_counts()

        status_counts = {status: 0 for status in JOB_STATUSES}
        for row in status_rows:
            status_counts[str(row["status"] or "unknown")] = int(
                row["article_count"] or 0
            )

        review_status_counts = {
            "unviewed": 0,
            "viewed": 0,
            "confirmed": 0,
            "needs_changes": 0,
        }
        for row in review_rows:
            review_status_counts[str(row["review_status"] or "unviewed")] = int(
                row["article_count"] or 0
            )

        total_articles = sum(status_counts.values())
        drafted_articles = status_counts.get("drafted", 0)
        published_articles = status_counts.get("published", 0)
        processing_articles = sum(
            status_counts.get(status, 0) for status in _PROCESSING_STATUSES
        )
        total_batches = int(batch_row["total_batches"] or 0) if batch_row else 0
        archived_batches = int(batch_row["archived_batches"] or 0) if batch_row else 0

        return {
            "date": today_text,
            "today_batches": int(batch_row["today_batches"] or 0) if batch_row else 0,
            "total_batches": total_batches,
            "active_batches": total_batches - archived_batches,
            "archived_batches": archived_batches,
            "total_articles": total_articles,
            "today_articles": (
                int(article_row["today_articles"] or 0) if article_row else 0
            ),
            "pending_review_articles": inbox_counts.get("review", 0),
            "ready_for_draft_articles": inbox_counts.get("ready_for_draft", 0),
            "write_failed_articles": inbox_counts.get("write_failed", 0),
            "generation_failed_articles": inbox_counts.get("generation_failed", 0),
            "drafted_articles": drafted_articles,
            "published_articles": published_articles,
            "drafted_or_published_articles": drafted_articles + published_articles,
            "failed_articles": status_counts.get("failed", 0),
            "cancelled_articles": status_counts.get("cancelled", 0),
            "processing_articles": processing_articles,
            "status_counts": dict(status_counts),
            "review_status_counts": dict(review_status_counts),
        }


def _date_text(value: date | str | None) -> str:
    if value is None:
        return business_date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError("today must use YYYY-MM-DD format") from exc
