from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from app.db import Database
from app.services.failures import classify_job_failure, sanitize_failure_text
from app.workflows.errors import JobCancelled

T = TypeVar("T")
_HEARTBEAT_INTERVAL_SECONDS = 10.0
_MAX_RETRY_BACKOFF_SECONDS = 15 * 60


def run_tracked_job_stage(
    db: Database,
    job_id: int,
    stage: str,
    operation: Callable[[], T],
    *,
    model_id: str | None = None,
) -> T:
    """Execute one pipeline stage and persist its outcome for batch jobs."""

    current = db.get_job(job_id)
    if not current:
        raise ValueError(f"Job not found: {job_id}")
    meta = dict(current.get("meta") or {})
    batch_id = str(meta.get("batch_id") or "").strip()
    if not batch_id:
        return operation()
    attempt = db.create_job_attempt(
        batch_id=batch_id,
        job_id=job_id,
        stage=stage,
        model_id=(
            str(model_id).strip()
            if model_id is not None
            else str(meta.get("selected_model_id") or "").strip()
        )
        or None,
    )
    heartbeat_stop = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_attempt,
        args=(db, int(attempt["id"]), heartbeat_stop),
        name=f"job-attempt-heartbeat-{attempt['id']}",
        daemon=True,
    )
    heartbeat.start()
    try:
        result = operation()
    except JobCancelled:
        _stop_heartbeat(heartbeat_stop, heartbeat)
        db.finish_job_attempt(
            int(attempt["id"]),
            status="cancelled",
            error_code="job.cancelled",
            error="用户已请求停止生成",
        )
        raise
    except Exception as exc:
        _stop_heartbeat(heartbeat_stop, heartbeat)
        failure = classify_job_failure(exc, step=stage, status="failed")
        next_retry_at = retry_backoff_at(
            failure,
            attempt_no=int(attempt.get("attempt_no") or 1),
            error=exc,
        )
        db.finish_job_attempt(
            int(attempt["id"]),
            status="failed",
            error_code=str((failure or {}).get("code") or ""),
            error=sanitize_failure_text(exc),
            next_retry_at=next_retry_at,
        )
        raise
    _stop_heartbeat(heartbeat_stop, heartbeat)
    db.finish_job_attempt(int(attempt["id"]), status="succeeded")
    return result


def _heartbeat_attempt(
    db: Database,
    attempt_id: int,
    stop: threading.Event,
) -> None:
    while not stop.wait(_HEARTBEAT_INTERVAL_SECONDS):
        try:
            if not db.heartbeat_job_attempt(attempt_id):
                return
        except Exception:  # noqa: BLE001
            # A transient heartbeat failure must not replace the actual model
            # or rendering result. The lease will be retried on the next tick.
            continue


def _stop_heartbeat(
    stop: threading.Event,
    thread: threading.Thread,
) -> None:
    stop.set()
    thread.join(timeout=1.0)


def retry_backoff_at(
    failure: dict[str, object] | None,
    *,
    attempt_no: int,
    error: object = "",
    now: datetime | None = None,
) -> str | None:
    """Return an exponential retry window for provider throttling and timeouts."""

    code = str((failure or {}).get("code") or "").strip().casefold()
    raw = str(error or "").strip().casefold()
    if code.endswith(".rate_limited") or any(
        marker in raw for marker in ("http 429", "rate limit", "too many requests")
    ):
        base_seconds = 60
    elif code.endswith((".timeout", ".ambiguous_timeout")) or any(
        marker in raw for marker in ("timeout", "timed out", "read timed")
    ):
        base_seconds = 30
    else:
        return None
    exponent = max(0, min(int(attempt_no) - 1, 5))
    delay_seconds = min(
        _MAX_RETRY_BACKOFF_SECONDS,
        base_seconds * (2**exponent),
    )
    retry_at = (now or datetime.now(UTC)).astimezone(UTC)
    retry_at += timedelta(seconds=delay_seconds)
    return retry_at.isoformat(timespec="microseconds")


__all__ = ["retry_backoff_at", "run_tracked_job_stage"]
