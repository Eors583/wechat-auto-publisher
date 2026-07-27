from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.db import JOB_STATUSES, Database


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
        with self.db.connect() as conn:
            batch_row = conn.execute(
                """
                SELECT COUNT(*) AS total_batches,
                       SUM(CASE WHEN substr(created_at, 1, 10) = ? THEN 1 ELSE 0 END)
                           AS today_batches,
                       SUM(CASE WHEN archived_at IS NOT NULL THEN 1 ELSE 0 END)
                           AS archived_batches
                FROM batches
                """,
                (today_text,),
            ).fetchone()
            status_rows = conn.execute(
                """
                SELECT j.status, COUNT(*) AS article_count
                FROM batch_jobs AS bj
                JOIN jobs AS j ON j.id = bj.job_id
                GROUP BY j.status
                """
            ).fetchall()
            article_row = conn.execute(
                """
                SELECT COUNT(*) AS total_articles,
                       SUM(CASE WHEN substr(j.created_at, 1, 10) = ? THEN 1 ELSE 0 END)
                           AS today_articles
                FROM batch_jobs AS bj
                JOIN jobs AS j ON j.id = bj.job_id
                """,
                (today_text,),
            ).fetchone()
            review_rows = conn.execute(
                """
                SELECT bj.review_status, COUNT(*) AS article_count
                FROM batch_jobs AS bj
                JOIN jobs AS j ON j.id = bj.job_id
                GROUP BY bj.review_status
                """
            ).fetchall()

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
            "pending_review_articles": status_counts.get("ready_for_review", 0),
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
        return datetime.now().astimezone().date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError("today must use YYYY-MM-DD format") from exc
