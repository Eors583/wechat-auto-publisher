from __future__ import annotations

from app.db import Database
from app.services import AnalyticsService


def _attach_job(
    db: Database,
    batch_id: str,
    account_id: str,
    status: str,
    *,
    review_status: str = "unviewed",
) -> int:
    job_id = db.create_job(topic="统计测试", source="test")
    db.update_job(job_id, status=status)
    db.attach_batch_job(batch_id, job_id, account_id, f"公众号{account_id}")
    if review_status != "unviewed":
        db.update_batch_job_review(batch_id, job_id, review_status)
    return job_id


def test_overview_matches_batch_article_statistics(tmp_path) -> None:
    db = Database(tmp_path / "analytics.db")
    db.create_batch("today-batch", topic="今日批次")
    db.create_batch("old-batch", topic="历史批次")
    with db.connect() as conn:
        conn.execute(
            "UPDATE batches SET created_at = ? WHERE id = ?",
            ("2026-07-21T16:30:00+00:00", "today-batch"),
        )
        conn.execute(
            "UPDATE batches SET created_at = ?, archived_at = ? WHERE id = ?",
            (
                "2026-07-21T01:00:00+00:00",
                "2026-07-22T02:00:00+00:00",
                "old-batch",
            ),
        )

    _attach_job(db, "today-batch", "a", "ready_for_review", review_status="viewed")
    _attach_job(db, "today-batch", "b", "drafted", review_status="confirmed")
    _attach_job(db, "today-batch", "c", "published", review_status="confirmed")
    _attach_job(db, "today-batch", "d", "failed")
    _attach_job(db, "old-batch", "e", "rewriting")
    _attach_job(db, "old-batch", "f", "cancelled")

    # Standalone legacy jobs were never displayed by the batch-based data page.
    standalone_id = db.create_job(topic="旧 CLI 任务", source="cli")
    db.update_job(standalone_id, status="failed")

    overview = AnalyticsService(db).get_overview(today="2026-07-22")

    assert overview["date"] == "2026-07-22"
    assert overview["today_batches"] == 1
    assert overview["total_batches"] == 2
    assert overview["active_batches"] == 1
    assert overview["archived_batches"] == 1
    assert overview["total_articles"] == 6
    assert overview["pending_review_articles"] == 1
    assert overview["drafted_articles"] == 1
    assert overview["published_articles"] == 1
    assert overview["drafted_or_published_articles"] == 2
    assert overview["failed_articles"] == 1
    assert overview["cancelled_articles"] == 1
    assert overview["processing_articles"] == 1
    assert overview["status_counts"]["rewriting"] == 1
    assert overview["review_status_counts"] == {
        "unviewed": 3,
        "viewed": 1,
        "confirmed": 2,
        "needs_changes": 0,
    }


def test_overview_uses_china_business_day_boundaries(tmp_path) -> None:
    db = Database(tmp_path / "analytics-timezone.db")
    db.create_batch("before-midnight-utc", topic="北京时间当日")
    db.create_batch("after-business-day", topic="北京时间次日")
    with db.connect() as conn:
        conn.execute(
            "UPDATE batches SET created_at = ? WHERE id = ?",
            ("2026-07-21T16:00:00+00:00", "before-midnight-utc"),
        )
        conn.execute(
            "UPDATE batches SET created_at = ? WHERE id = ?",
            ("2026-07-22T16:00:00+00:00", "after-business-day"),
        )

    overview = AnalyticsService(db).get_overview(today="2026-07-22")

    assert overview["today_batches"] == 1


def test_empty_overview_is_complete_and_json_safe(tmp_path) -> None:
    overview = AnalyticsService(Database(tmp_path / "empty.db")).get_overview(
        today="2026-07-22"
    )

    assert overview["today_batches"] == 0
    assert overview["total_batches"] == 0
    assert overview["total_articles"] == 0
    assert overview["drafted_or_published_articles"] == 0
    assert overview["status_counts"]["ready_for_review"] == 0


def test_overview_rejects_ambiguous_date_text(tmp_path) -> None:
    service = AnalyticsService(Database(tmp_path / "invalid-date.db"))

    try:
        service.get_overview(today="07/22/2026")
    except ValueError as exc:
        assert "YYYY-MM-DD" in str(exc)
    else:
        raise AssertionError("ambiguous dates must be rejected")
