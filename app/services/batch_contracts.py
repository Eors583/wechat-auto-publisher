from __future__ import annotations

from typing import Any

from app.ai import (
    SUBTITLE_CANDIDATE_COUNT,
    TITLE_CANDIDATE_COUNT,
    clean_candidate_list,
    clean_candidate_text,
)


TERMINAL_STATUSES = {
    "ready_for_review",
    "drafted",
    "published",
    "failed",
    "cancelled",
}


def batch_progress(jobs: list[dict[str, Any]]) -> dict[str, int]:
    """Build the stable progress contract exposed to UI, API and Feishu."""
    return {
        "total": len(jobs),
        "completed": sum(
            1 for job in jobs if str(job.get("status")) in TERMINAL_STATUSES
        ),
        "ready_for_review": sum(
            1 for job in jobs if job.get("status") == "ready_for_review"
        ),
        "drafted": sum(1 for job in jobs if job.get("status") == "drafted"),
        "failed": sum(1 for job in jobs if job.get("status") == "failed"),
        "confirmed": sum(
            1 for job in jobs if effective_review_status(job) == "confirmed"
        ),
        "unconfirmed": sum(
            1
            for job in jobs
            if job.get("status") == "ready_for_review"
            and effective_review_status(job) != "confirmed"
        ),
        "review_total": sum(
            1
            for job in jobs
            if str(job.get("status") or "")
            in {"ready_for_review", "injecting", "drafted", "published"}
        ),
        "reviewed": sum(
            1
            for job in jobs
            if effective_review_status(job) in {"confirmed", "drafted"}
        ),
    }


def public_job(job: dict[str, Any], *, include_content: bool) -> dict[str, Any]:
    """Project an internal DB job into the public batch API representation."""
    meta = dict(job.get("meta") or {})
    result = {
        "id": int(job["id"]),
        "status": str(job.get("status") or ""),
        "step": str(job.get("step") or ""),
        "account_id": str(
            meta.get("official_account_id") or job.get("account_id") or ""
        ),
        "account_name": str(
            meta.get("official_account_name") or job.get("account_name") or ""
        ),
        "model_name": str(meta.get("selected_model_name") or ""),
        "titles": clean_candidate_list(
            list(job.get("title_candidates") or job.get("titles") or []),
            limit=TITLE_CANDIDATE_COUNT,
        ),
        "subtitles": clean_candidate_list(
            list(job.get("subtitles") or []),
            limit=SUBTITLE_CANDIDATE_COUNT,
        ),
        "selected_title": clean_candidate_text(
            str(job.get("selected_title") or "")
        ) or None,
        "selected_subtitle": clean_candidate_text(
            str(job.get("selected_subtitle") or "")
        ) or None,
        "draft_media_id": job.get("draft_media_id"),
        "error": job.get("error"),
        "review_status": effective_review_status(job),
        "viewed_at": job.get("viewed_at"),
        "confirmed_at": job.get("confirmed_at"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }
    if include_content:
        result["body"] = str(job.get("body") or "")
        result["html_content"] = str(job.get("html_content") or "")
        result["digest"] = str(job.get("digest") or "")
        result["thumb_media_id"] = str(job.get("thumb_media_id") or "")
        result["meta"] = meta
    return result


def effective_review_status(job: dict[str, Any]) -> str:
    status = str(job.get("status") or "")
    if status in {"drafted", "published"}:
        return "drafted"
    if status == "failed" and str(job.get("step") or "") == "inject":
        return "write_failed"
    return str(job.get("review_status") or "unviewed")


def effective_batch_status(jobs: list[dict[str, Any]], stored: str = "") -> str:
    if not jobs:
        return stored or "pending"
    statuses = {str(job.get("status") or "") for job in jobs}
    active = {
        "pending", "ingesting", "rewriting", "title_optimizing", "rendering"
    }
    if statuses & active:
        return "processing"
    if "injecting" in statuses:
        return "injecting"
    if statuses <= {"drafted", "published"}:
        return "drafted"
    if statuses == {"ready_for_review"}:
        return "ready_for_review"
    if statuses == {"cancelled"}:
        return "cancelled"
    if statuses == {"failed"}:
        return "failed"
    return "partial_failed"
