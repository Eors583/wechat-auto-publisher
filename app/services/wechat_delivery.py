from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.db import Database
from app.wechat.client import WeChatAPIError, WeChatClient
from app.wechat.draft import add_draft, batchget_drafts
from app.wechat.errors import WeChatHTTPError, friendly_wechat_error

HEALTHY = "healthy"
UNHEALTHY = "unhealthy"
HEALTH_CACHE_TTL = timedelta(minutes=5)

DRAFT_DELIVERY_STATUSES = frozenset(
    {"queued", "running", "succeeded", "needs_reconcile", "failed"}
)
DRAFT_RUNNING_STALE_AFTER = timedelta(minutes=2)
DRAFT_RECONCILE_SAFE_WAIT = timedelta(minutes=2)
DRAFT_RECONCILE_LOOKBACK = timedelta(minutes=15)
DRAFT_RECONCILE_LIMIT = 100
DRAFT_RECONCILE_PAGE_SIZE = 20

_ARTICLE_FIELDS = (
    "title",
    "author",
    "digest",
    "content",
    "content_source_url",
    "thumb_media_id",
    "need_open_comment",
    "only_fans_can_comment",
)

ConnectionProbe = Callable[[], dict[str, Any]]


class DraftDeliveryError(RuntimeError):
    """Base error for persistent WeChat draft delivery."""


class DraftDeliveryInProgress(DraftDeliveryError):
    """The same idempotent delivery is already running in another worker."""


class DraftDeliveryNeedsReconcile(DraftDeliveryError):
    """A draft may exist remotely and must not be submitted again blindly."""


@dataclass(frozen=True)
class DraftReconcileScan:
    """Evidence collected while scanning recent remote drafts."""

    media_id: str | None
    complete: bool
    trusted: bool
    ambiguous: bool
    scanned: int
    reason: str = ""

    @property
    def can_release(self) -> bool:
        return (
            self.media_id is None
            and self.complete
            and self.trusted
            and not self.ambiguous
        )


def get_or_probe_wechat_connection_health(
    db: Database,
    account_id: str,
    probe: ConnectionProbe,
    *,
    force: bool = False,
    mode: str = "direct",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a five-minute cached connection probe for one official account.

    ``probe`` returns ``status``, optional ``details`` and optional ``error``.
    Both healthy and unhealthy results are cached so repeated readiness checks
    cannot amplify a relay outage.
    """

    checked_at = _as_utc(now or _utc_now())
    if not force:
        cached = db.get_wechat_connection_health(str(account_id))
        if cached and _is_unexpired(cached.get("expires_at"), checked_at):
            return {**cached, "cached": True}

    try:
        result = dict(probe() or {})
        status = str(result.get("status") or "").strip().casefold()
        if status not in {HEALTHY, UNHEALTHY}:
            status = HEALTHY if bool(result.get("ok")) else UNHEALTHY
        details = result.get("details")
        if not isinstance(details, dict):
            details = {}
        error = str(result.get("error") or "").strip() or None
        connection_mode = str(result.get("mode") or mode or "direct").strip()
        raw_latency = result.get("latency_ms")
        latency_ms = max(0, int(raw_latency)) if raw_latency is not None else None
        last_error_code = str(result.get("last_error_code") or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        status = UNHEALTHY
        details = {}
        error = friendly_wechat_error(exc)
        connection_mode = str(mode or "direct").strip()
        latency_ms = None
        last_error_code = None

    expires_at = checked_at + HEALTH_CACHE_TTL
    db.upsert_wechat_connection_health(
        str(account_id),
        status=status,
        checked_at=checked_at.isoformat(timespec="microseconds"),
        expires_at=expires_at.isoformat(timespec="microseconds"),
        details=details,
        error=error,
        mode=connection_mode,
        latency_ms=latency_ms,
        last_error_code=last_error_code,
    )
    stored = db.get_wechat_connection_health(str(account_id))
    if stored:
        return {**stored, "cached": False}
    return {
        "account_id": str(account_id),
        "status": status,
        "checked_at": checked_at.isoformat(timespec="microseconds"),
        "expires_at": expires_at.isoformat(timespec="microseconds"),
        "details": details,
        "error": error,
        "mode": connection_mode,
        "latency_ms": latency_ms,
        "last_error_code": last_error_code,
        "cached": False,
    }


def invalidate_wechat_connection_health(db: Database, account_id: str) -> None:
    """Invalidate one account after credentials, relay settings or I/O fail."""

    db.invalidate_wechat_connection_health(str(account_id))


def draft_content_fingerprint(articles: Sequence[dict[str, Any]]) -> str:
    """Hash only fields sent to ``draft/add`` using a stable JSON encoding."""

    normalized = [
        {field: _json_scalar(article.get(field)) for field in _ARTICLE_FIELDS}
        for article in articles
    ]
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def draft_idempotency_key(
    *,
    account_id: str,
    job_id: int,
    content_revision: int,
    content_fingerprint: str,
) -> str:
    payload = (
        "wechat-draft:v2:"
        f"{account_id!s}:{int(job_id)}:{max(0, int(content_revision))}:"
        f"{content_fingerprint}"
    ).encode()
    return f"draft_{hashlib.sha256(payload).hexdigest()}"


def deliver_draft_once(
    db: Database,
    client: WeChatClient,
    *,
    job_id: int,
    account_id: str,
    articles: Sequence[dict[str, Any]],
    fingerprint_articles: Sequence[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> str:
    """Create one WeChat draft with persistent idempotency and reconciliation.

    The idempotency fingerprint may intentionally cover only the stable primary
    article. Secondary articles are selected from a moving draft library and
    must not produce a new key when the same job is resumed after an uncertain
    transport result.
    """

    submitted_articles = [dict(item) for item in articles]
    if not submitted_articles:
        raise ValueError("At least one article is required for WeChat draft delivery")
    stable_articles = [
        dict(item) for item in (fingerprint_articles or submitted_articles)
    ]
    content_fingerprint = draft_content_fingerprint(stable_articles)
    stored_job = db.get_job(int(job_id)) or {}
    content_revision = max(0, int(stored_job.get("content_revision") or 0))
    key = draft_idempotency_key(
        account_id=str(account_id),
        job_id=int(job_id),
        content_revision=content_revision,
        content_fingerprint=content_fingerprint,
    )
    row = db.claim_draft_delivery(
        idempotency_key=key,
        job_id=int(job_id),
        account_id=str(account_id),
        content_fingerprint=content_fingerprint,
        content_revision=content_revision,
    )
    return _continue_draft_delivery(
        db,
        client,
        row=row,
        articles=submitted_articles,
        account_id=str(account_id),
        now=_as_utc(now or _utc_now()),
    )


def find_recent_matching_draft(
    client: WeChatClient,
    articles: Sequence[dict[str, Any]],
    *,
    delivery_created_at: str | datetime | None,
    now: datetime | None = None,
    limit: int = DRAFT_RECONCILE_LIMIT,
) -> str | None:
    """Find a recent draft only from strict title, cover and body evidence."""

    return scan_recent_matching_drafts(
        client,
        articles,
        delivery_created_at=delivery_created_at,
        now=now,
        limit=limit,
    ).media_id


def scan_recent_matching_drafts(
    client: WeChatClient,
    articles: Sequence[dict[str, Any]],
    *,
    delivery_created_at: str | datetime | None,
    now: datetime | None = None,
    limit: int = DRAFT_RECONCILE_LIMIT,
) -> DraftReconcileScan:
    """Page through recent drafts and report whether absence is trustworthy.

    A positive match requires an exact normalized primary body in addition to
    title and cover. A negative result can release an explicit retry only when
    the scan reached the delivery time boundary (or exhausted the remote list)
    and every relevant row included both ``update_time`` and full content.
    """

    if not articles:
        return DraftReconcileScan(
            media_id=None,
            complete=False,
            trusted=False,
            ambiguous=False,
            scanned=0,
            reason="expected article is missing",
        )
    expected_primary = dict(articles[0])
    expected_title = _clean_text(expected_primary.get("title"))
    expected_cover = _clean_text(expected_primary.get("thumb_media_id"))
    expected_content = _normalized_content(expected_primary.get("content"))
    if not expected_title or not expected_cover or not expected_content:
        return DraftReconcileScan(
            media_id=None,
            complete=False,
            trusted=False,
            ambiguous=False,
            scanned=0,
            reason="expected title, cover or body is missing",
        )

    current_time = _as_utc(now or _utc_now())
    created_at = _parse_datetime(delivery_created_at)
    lower_bound = (
        created_at - timedelta(minutes=1)
        if created_at
        else current_time - DRAFT_RECONCILE_LOOKBACK
    )
    max_items = max(1, min(200, int(limit)))
    offset = 0
    scanned = 0
    trusted = True
    complete = False
    reasons: list[str] = []
    candidates: list[tuple[int, str]] = []
    observed_total_count: int | None = None

    while scanned < max_items:
        page_size = min(DRAFT_RECONCILE_PAGE_SIZE, max_items - scanned)
        data = batchget_drafts(
            client,
            offset=offset,
            count=page_size,
            no_content=0,
        )
        raw_items = data.get("item")
        if not isinstance(raw_items, list):
            trusted = False
            reasons.append("draft page did not contain an item list")
            break
        items = raw_items
        raw_total_count = data.get("total_count")
        total_count = _nonnegative_int(raw_total_count)
        if raw_total_count is not None and total_count is None:
            trusted = False
            reasons.append("draft total_count was invalid")
        elif total_count is not None:
            if observed_total_count is not None and total_count != observed_total_count:
                trusted = False
                reasons.append("draft total_count changed during pagination")
            observed_total_count = total_count

        raw_item_count = data.get("item_count")
        item_count = _nonnegative_int(raw_item_count)
        if raw_item_count is not None and (
            item_count is None or item_count != len(items)
        ):
            trusted = False
            reasons.append("draft item_count was inconsistent")
        if len(items) > page_size:
            trusted = False
            reasons.append("draft page exceeded the requested page size")
            break
        if not items:
            if total_count is None or total_count == offset:
                complete = True
            else:
                trusted = False
                reasons.append("draft page was empty before total_count was exhausted")
            break

        reached_time_boundary = False
        for item in items:
            scanned += 1
            if not isinstance(item, dict):
                trusted = False
                reasons.append("draft row was malformed")
                continue

            update_time = _coerce_epoch(item.get("update_time"))
            if update_time is None:
                trusted = False
                reasons.append("draft row was missing update_time")
                continue
            updated_at = datetime.fromtimestamp(update_time, tz=UTC)
            if updated_at > current_time + timedelta(minutes=5):
                trusted = False
                reasons.append("draft row had a future update_time")
                continue
            if updated_at < lower_bound:
                complete = True
                reached_time_boundary = True
                break

            media_id = str(item.get("media_id") or "").strip()
            content = item.get("content")
            news_items = (
                content.get("news_item")
                if isinstance(content, dict)
                else item.get("news_item")
            )
            if (
                not media_id
                or not isinstance(news_items, list)
                or not news_items
                or not isinstance(news_items[0], dict)
            ):
                trusted = False
                reasons.append("recent draft row was missing full content")
                continue
            remote_primary = news_items[0]
            remote_title = _clean_text(remote_primary.get("title"))
            remote_cover = _clean_text(remote_primary.get("thumb_media_id"))
            remote_body = remote_primary.get("content")
            if not remote_title or not remote_cover:
                trusted = False
                reasons.append("recent draft row was missing title or cover")
                continue
            if not isinstance(remote_body, str):
                trusted = False
                reasons.append("recent draft row had an invalid primary body")
                continue
            remote_content = _normalized_content(remote_body)
            if not remote_content:
                trusted = False
                reasons.append("recent draft row was missing primary body")
                continue
            if (
                _same_primary_identity(expected_primary, remote_primary)
                and remote_content == expected_content
            ):
                candidates.append((update_time, media_id))

        offset += len(items)
        if reached_time_boundary:
            break

        if total_count is not None and total_count < offset:
            trusted = False
            reasons.append("draft total_count was inconsistent")
            break
        if total_count is not None and offset >= total_count:
            complete = True
            break
        if total_count is None and len(items) < page_size:
            complete = True
            break
        if scanned >= max_items:
            reasons.append("draft scan reached its safety limit")
            break

    candidates.sort(reverse=True)
    media_id: str | None = None
    ambiguous = False
    if len(candidates) > 1:
        ambiguous = True
        reasons.append("multiple matching recent drafts were found")
    elif candidates and complete and trusted:
        media_id = candidates[0][1]
    elif candidates:
        reasons.append(
            "matching draft was found during an incomplete or untrusted scan"
        )

    return DraftReconcileScan(
        media_id=media_id,
        complete=complete,
        trusted=trusted,
        ambiguous=ambiguous,
        scanned=scanned,
        reason="; ".join(dict.fromkeys(reasons)),
    )


def is_uncertain_draft_error(exc: Exception) -> bool:
    """Whether ``draft/add`` may have reached WeChat despite the exception."""

    if isinstance(exc, WeChatAPIError):
        return False
    if isinstance(exc, WeChatHTTPError):
        return int(exc.status_code) >= 500
    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.TransportError,
            TimeoutError,
            ConnectionError,
            OSError,
        ),
    ):
        return True
    return isinstance(exc, RuntimeError) and "missing media_id" in str(exc)


def _continue_draft_delivery(
    db: Database,
    client: WeChatClient,
    *,
    row: dict[str, Any],
    articles: list[dict[str, Any]],
    account_id: str,
    now: datetime,
) -> str:
    for _attempt in range(4):
        status = str(row.get("status") or "")
        key = str(row.get("idempotency_key") or "")
        if status not in DRAFT_DELIVERY_STATUSES or not key:
            raise DraftDeliveryError("Invalid persisted WeChat draft delivery state")
        if status == "succeeded":
            media_id = str(row.get("draft_media_id") or "").strip()
            if not media_id:
                raise DraftDeliveryError(
                    "Succeeded WeChat draft delivery is missing draft_media_id"
                )
            return media_id
        if status == "needs_reconcile":
            return _reconcile_delivery(
                db,
                client,
                row=row,
                articles=articles,
                account_id=account_id,
                now=now,
                allow_release=True,
            )
        if status == "running":
            if not _running_is_stale(row, now):
                raise DraftDeliveryInProgress(
                    "The same WeChat draft delivery is already running"
                )
            transitioned = db.transition_draft_delivery(
                key,
                from_statuses=("running",),
                status="needs_reconcile",
                error=(
                    "Previous draft delivery stopped while the remote result "
                    "was unknown"
                ),
            )
            if transitioned:
                return _reconcile_delivery(
                    db,
                    client,
                    row=transitioned,
                    articles=articles,
                    account_id=account_id,
                    now=now,
                    allow_release=True,
                    uncertain_since=row.get("updated_at"),
                )
        elif status in {"queued", "failed"}:
            transitioned = db.transition_draft_delivery(
                key,
                from_statuses=(status,),
                status="running",
                error=None,
            )
            if transitioned:
                return _submit_claimed_delivery(
                    db,
                    client,
                    row=transitioned,
                    articles=articles,
                    account_id=account_id,
                    now=now,
                )
        latest = db.get_draft_delivery(key)
        if not latest:
            raise DraftDeliveryError("Persisted WeChat draft delivery disappeared")
        row = latest
    raise DraftDeliveryError("WeChat draft delivery state changed too frequently")


def _submit_claimed_delivery(
    db: Database,
    client: WeChatClient,
    *,
    row: dict[str, Any],
    articles: list[dict[str, Any]],
    account_id: str,
    now: datetime,
) -> str:
    key = str(row["idempotency_key"])
    try:
        media_id = add_draft(client, articles)
    except Exception as exc:
        _invalidate_after_delivery_failure(db, account_id)
        if is_uncertain_draft_error(exc):
            transitioned = db.transition_draft_delivery(
                key,
                from_statuses=("running",),
                status="needs_reconcile",
                error=friendly_wechat_error(exc),
            )
            current = transitioned or db.get_draft_delivery(key) or row
            return _reconcile_delivery(
                db,
                client,
                row=current,
                articles=articles,
                account_id=account_id,
                now=now,
                cause=exc,
                allow_release=False,
            )
        db.transition_draft_delivery(
            key,
            from_statuses=("running",),
            status="failed",
            error=friendly_wechat_error(exc),
        )
        raise

    transitioned = db.transition_draft_delivery(
        key,
        from_statuses=("running", "needs_reconcile"),
        status="succeeded",
        draft_media_id=media_id,
        error=None,
    )
    if transitioned:
        _mark_successful_write(db, account_id, now)
        return media_id
    latest = db.get_draft_delivery(key)
    if latest and str(latest.get("status") or "") == "succeeded":
        _mark_successful_write(db, account_id, now)
        return str(latest.get("draft_media_id") or media_id)
    raise DraftDeliveryError(
        "WeChat accepted the draft but its local delivery state could not be finalized"
    )


def _reconcile_delivery(
    db: Database,
    client: WeChatClient,
    *,
    row: dict[str, Any],
    articles: list[dict[str, Any]],
    account_id: str,
    now: datetime,
    allow_release: bool = False,
    cause: Exception | None = None,
    uncertain_since: Any = None,
) -> str:
    key = str(row.get("idempotency_key") or "")
    try:
        scan = scan_recent_matching_drafts(
            client,
            articles,
            delivery_created_at=row.get("created_at"),
            now=now,
        )
    except Exception as reconcile_exc:  # noqa: BLE001
        _invalidate_after_delivery_failure(db, account_id)
        message = (
            "WeChat draft result is uncertain and recent drafts could not be "
            f"checked safely: {friendly_wechat_error(reconcile_exc)}"
        )
        raise DraftDeliveryNeedsReconcile(message) from (cause or reconcile_exc)

    media_id = scan.media_id
    if not media_id:
        if (
            allow_release
            and scan.can_release
            and _safe_reconcile_wait_elapsed(
                uncertain_since or row.get("updated_at") or row.get("created_at"),
                now,
            )
        ):
            released = db.transition_draft_delivery(
                key,
                from_statuses=("needs_reconcile",),
                status="queued",
                error=(
                    "No matching recent draft was found after the safety "
                    "window; an explicit retry may submit again"
                ),
            )
            if released:
                return _continue_draft_delivery(
                    db,
                    client,
                    row=released,
                    articles=articles,
                    account_id=account_id,
                    now=now,
                )
            latest = db.get_draft_delivery(key)
            if latest and str(latest.get("status") or "") == "succeeded":
                return str(latest.get("draft_media_id") or "")
        message = (
            "WeChat draft result is uncertain; no unique recent matching draft "
            "was found from a complete, trusted scan. Automatic resubmission "
            "is disabled."
        )
        if scan.reason:
            message += f" Reconciliation detail: {scan.reason}."
        if cause:
            message += f" Original error: {friendly_wechat_error(cause)}"
        raise DraftDeliveryNeedsReconcile(message) from cause

    transitioned = db.transition_draft_delivery(
        key,
        from_statuses=("needs_reconcile", "running"),
        status="succeeded",
        draft_media_id=media_id,
        error=None,
        reconciled_at=now.isoformat(timespec="microseconds"),
    )
    if transitioned:
        _mark_successful_write(db, account_id, now)
        return media_id
    latest = db.get_draft_delivery(key)
    if latest and str(latest.get("status") or "") == "succeeded":
        _mark_successful_write(db, account_id, now)
        return str(latest.get("draft_media_id") or media_id)
    raise DraftDeliveryNeedsReconcile(
        "A matching WeChat draft was found but local reconciliation did not finish"
    )


def _invalidate_after_delivery_failure(db: Database, account_id: str) -> None:
    try:
        invalidate_wechat_connection_health(db, account_id)
    except Exception:  # noqa: BLE001
        # A cache invalidation failure must not hide the delivery outcome.
        return


def _mark_successful_write(
    db: Database,
    account_id: str,
    written_at: datetime,
) -> None:
    try:
        db.mark_wechat_connection_write_success(
            account_id,
            written_at=written_at.isoformat(timespec="microseconds"),
        )
    except Exception:  # noqa: BLE001
        # Health telemetry must never turn an accepted draft into an error.
        return


def _same_primary_identity(expected: dict[str, Any], remote: dict[str, Any]) -> bool:
    expected_title = _clean_text(expected.get("title"))
    expected_cover = _clean_text(expected.get("thumb_media_id"))
    return bool(
        expected_title
        and expected_cover
        and expected_title == _clean_text(remote.get("title"))
        and expected_cover == _clean_text(remote.get("thumb_media_id"))
    )


def _running_is_stale(row: dict[str, Any], now: datetime) -> bool:
    updated_at = _parse_datetime(row.get("updated_at"))
    if updated_at is None:
        return True
    return updated_at <= now - DRAFT_RUNNING_STALE_AFTER


def _safe_reconcile_wait_elapsed(value: Any, now: datetime) -> bool:
    uncertain_at = _parse_datetime(value)
    if uncertain_at is None:
        return False
    return uncertain_at <= now - DRAFT_RECONCILE_SAFE_WAIT


def _is_unexpired(value: Any, now: datetime) -> bool:
    expires_at = _parse_datetime(value)
    return bool(expires_at and expires_at > now)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _coerce_epoch(value: Any) -> int | None:
    try:
        epoch = int(value)
    except (TypeError, ValueError):
        return None
    return epoch if epoch > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _json_scalar(value: Any) -> str | int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if value is None:
        return ""
    return str(value)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalized_content(value: Any) -> str:
    collapsed = re.sub(r"\s+", " ", str(value or "")).strip()
    return re.sub(r">\s+<", "><", collapsed)


__all__ = [
    "DRAFT_DELIVERY_STATUSES",
    "DRAFT_RECONCILE_LIMIT",
    "DRAFT_RECONCILE_SAFE_WAIT",
    "HEALTHY",
    "HEALTH_CACHE_TTL",
    "UNHEALTHY",
    "DraftDeliveryError",
    "DraftDeliveryInProgress",
    "DraftDeliveryNeedsReconcile",
    "DraftReconcileScan",
    "deliver_draft_once",
    "draft_content_fingerprint",
    "draft_idempotency_key",
    "find_recent_matching_draft",
    "get_or_probe_wechat_connection_health",
    "invalidate_wechat_connection_health",
    "is_uncertain_draft_error",
    "scan_recent_matching_drafts",
]
