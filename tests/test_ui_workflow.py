from __future__ import annotations

from app.ui.workflow import next_review_job, normalize_workflow_stage


def test_workflow_stage_falls_back_to_content() -> None:
    assert normalize_workflow_stage("review") == "review"
    assert normalize_workflow_stage("unknown") == "content"


def test_next_review_job_starts_after_current_and_wraps() -> None:
    jobs = [
        {"id": 11, "status": "ready_for_review", "review_status": "unviewed"},
        {"id": 12, "status": "ready_for_review", "review_status": "confirmed"},
        {"id": 13, "status": "ready_for_review", "review_status": "viewed"},
    ]

    assert next_review_job(jobs, current_job_id=11)["id"] == 13
    assert next_review_job(jobs, current_job_id=13)["id"] == 11


def test_next_review_job_ignores_current_and_non_reviewable_rows() -> None:
    jobs = [
        {"id": 21, "status": "ready_for_review", "review_status": "unviewed"},
        {"id": 22, "status": "failed", "review_status": "unviewed"},
        {"id": 23, "status": "ready_for_review", "review_status": "confirmed"},
    ]

    assert next_review_job(jobs, current_job_id=21) is None
    assert next_review_job(jobs)["id"] == 21
