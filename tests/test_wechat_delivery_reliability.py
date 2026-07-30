from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.db import Database
from app.services.wechat_delivery import (
    DraftDeliveryNeedsReconcile,
    deliver_draft_once,
    get_or_probe_wechat_connection_health,
)
from app.services.wechat_relay_settings import save_wechat_relay_settings
from app.wechat.client import WeChatAPIError

ARTICLE = {
    "title": "A stable primary title",
    "author": "Author",
    "digest": "Digest",
    "content": "<section><p>Body</p></section>",
    "content_source_url": "",
    "thumb_media_id": "thumb-1",
    "need_open_comment": 0,
    "only_fans_can_comment": 0,
}


class _ScriptedClient:
    def __init__(
        self,
        *,
        add_results: list[Any] | None = None,
        draft_items: list[dict[str, Any]] | None = None,
    ) -> None:
        self.add_results = list(add_results or [])
        self.draft_items = list(draft_items or [])
        self.calls: Counter[str] = Counter()

    def request(
        self,
        _method: str,
        path: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.calls[path] += 1
        if path == "/cgi-bin/draft/add":
            if not self.add_results:
                raise AssertionError("unexpected draft/add call")
            result = self.add_results.pop(0)
            if isinstance(result, BaseException):
                raise result
            return {"media_id": str(result)}
        if path == "/cgi-bin/draft/batchget":
            payload = _kwargs.get("json_body") or {}
            offset = max(0, int(payload.get("offset") or 0))
            count = max(1, int(payload.get("count") or 20))
            page = self.draft_items[offset : offset + count]
            return {
                "total_count": len(self.draft_items),
                "item_count": len(page),
                "item": page,
            }
        raise AssertionError(f"unexpected WeChat path: {path}")


def _remote_draft(
    media_id: str,
    *,
    update_time: datetime,
    article: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "media_id": media_id,
        "update_time": int(update_time.timestamp()),
        "content": {"news_item": [dict(article or ARTICLE)]},
    }


def test_connection_health_is_cached_for_five_minutes_and_can_be_invalidated(
    tmp_path,
) -> None:
    db = Database(tmp_path / "health-cache.db")
    now = datetime.now(UTC)
    calls = 0

    def probe() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {
            "status": "healthy",
            "mode": "relay",
            "latency_ms": 37,
            "details": {"draft": {"reachable": True, "total_count": 3}},
        }

    first = get_or_probe_wechat_connection_health(db, "account-1", probe, now=now)
    cached = get_or_probe_wechat_connection_health(
        db, "account-1", probe, now=now + timedelta(minutes=4, seconds=59)
    )

    assert first["cached"] is False
    assert first["mode"] == "relay"
    assert first["latency_ms"] == 37
    assert cached["cached"] is True
    assert calls == 1

    forced = get_or_probe_wechat_connection_health(
        db,
        "account-1",
        probe,
        force=True,
        now=now + timedelta(minutes=1),
    )
    assert forced["cached"] is False
    assert calls == 2

    db.invalidate_wechat_connection_health("account-1")
    refreshed = get_or_probe_wechat_connection_health(
        db, "account-1", probe, now=now + timedelta(minutes=4, seconds=59)
    )
    assert refreshed["cached"] is False
    assert calls == 3

    expired = get_or_probe_wechat_connection_health(
        db, "account-1", probe, now=now + timedelta(minutes=10)
    )
    assert expired["cached"] is False
    assert calls == 4


def test_successful_delivery_is_persistently_deduplicated_by_primary_article(
    tmp_path,
) -> None:
    db = Database(tmp_path / "deduplicated-draft.db")
    first_secondary = {**ARTICLE, "title": "Secondary A", "thumb_media_id": "thumb-a"}
    later_secondary = {**ARTICLE, "title": "Secondary B", "thumb_media_id": "thumb-b"}
    client = _ScriptedClient(add_results=["draft-1"])

    first = deliver_draft_once(
        db,
        client,  # type: ignore[arg-type]
        job_id=_job(db),
        account_id="account-1",
        articles=[ARTICLE, first_secondary],
        fingerprint_articles=[ARTICLE],
    )
    job_id = _only_delivery(db)["job_id"]
    second = deliver_draft_once(
        db,
        client,  # type: ignore[arg-type]
        job_id=job_id,
        account_id="account-1",
        articles=[ARTICLE, later_secondary],
        fingerprint_articles=[ARTICLE],
    )

    assert first == second == "draft-1"
    assert client.calls["/cgi-bin/draft/add"] == 1
    delivery = _only_delivery(db)
    assert delivery["status"] == "succeeded"
    assert delivery["attempts"] == 1
    health = db.get_wechat_connection_health("account-1")
    assert health is not None
    assert health["last_successful_write_at"]
    successful_write_at = health["last_successful_write_at"]
    db.invalidate_wechat_connection_health("account-1")
    invalidated = db.get_wechat_connection_health("account-1")
    assert invalidated is not None
    assert invalidated["last_successful_write_at"] == successful_write_at


def test_new_article_revision_uses_a_new_delivery_identity(tmp_path) -> None:
    db = Database(tmp_path / "revision-aware-draft.db")
    job_id = _job(db)
    client = _ScriptedClient(add_results=["draft-v1", "draft-v2"])

    first = deliver_draft_once(
        db,
        client,  # type: ignore[arg-type]
        job_id=job_id,
        account_id="account-1",
        articles=[ARTICLE],
    )
    db.update_job(job_id, body="运营人员确认后的新文章版本")
    second = deliver_draft_once(
        db,
        client,  # type: ignore[arg-type]
        job_id=job_id,
        account_id="account-1",
        articles=[ARTICLE],
    )

    assert first == "draft-v1"
    assert second == "draft-v2"
    assert client.calls["/cgi-bin/draft/add"] == 2
    with db.connect() as conn:
        deliveries = conn.execute(
            "SELECT content_revision, status FROM draft_deliveries "
            "ORDER BY content_revision"
        ).fetchall()
    assert [int(row["content_revision"]) for row in deliveries] == [0, 1]
    assert [str(row["status"]) for row in deliveries] == [
        "succeeded",
        "succeeded",
    ]


def test_saving_global_relay_settings_expires_health_without_losing_last_write(
    tmp_path,
) -> None:
    db = Database(tmp_path / "relay-health-invalidation.db")
    timestamp = "2026-07-28T08:05:00+00:00"
    db.upsert_wechat_connection_health(
        "account-1",
        status="healthy",
        checked_at=timestamp,
        expires_at="2099-01-01T00:00:00+00:00",
        last_successful_write_at=timestamp,
    )

    save_wechat_relay_settings(
        db,
        enabled=False,
        gateway_url="",
        username="",
    )

    health = db.get_wechat_connection_health("account-1")
    assert health is not None
    assert health["status"] == "stale"
    assert health["last_successful_write_at"] == timestamp


def test_uncertain_transport_result_is_reconciled_without_resubmission(
    tmp_path,
) -> None:
    db = Database(tmp_path / "reconciled-draft.db")
    now = datetime.now(UTC)
    client = _ScriptedClient(
        add_results=[httpx.ReadTimeout("relay response was lost")],
        draft_items=[_remote_draft("remote-draft", update_time=now)],
    )

    media_id = deliver_draft_once(
        db,
        client,  # type: ignore[arg-type]
        job_id=_job(db),
        account_id="account-1",
        articles=[ARTICLE],
        now=now,
    )

    assert media_id == "remote-draft"
    assert client.calls["/cgi-bin/draft/add"] == 1
    assert client.calls["/cgi-bin/draft/batchget"] == 1
    delivery = _only_delivery(db)
    assert delivery["status"] == "succeeded"
    assert delivery["reconciled_at"]


def test_uncertain_transport_result_finds_exact_match_on_second_page(
    tmp_path,
) -> None:
    db = Database(tmp_path / "second-page-reconcile.db")
    now = datetime.now(UTC)
    first_page = [
        _remote_draft(
            f"other-{index}",
            update_time=now - timedelta(seconds=index),
            article={**ARTICLE, "title": f"Other title {index}"},
        )
        for index in range(20)
    ]
    client = _ScriptedClient(
        add_results=[httpx.ReadTimeout("relay response was lost")],
        draft_items=[
            *first_page,
            _remote_draft(
                "remote-second-page",
                update_time=now - timedelta(seconds=20),
            ),
        ],
    )

    media_id = deliver_draft_once(
        db,
        client,  # type: ignore[arg-type]
        job_id=_job(db),
        account_id="account-1",
        articles=[ARTICLE],
        now=now,
    )

    assert media_id == "remote-second-page"
    assert client.calls["/cgi-bin/draft/add"] == 1
    assert client.calls["/cgi-bin/draft/batchget"] == 2
    assert _only_delivery(db)["status"] == "succeeded"


def test_same_title_and_cover_with_different_body_is_not_reconciled(
    tmp_path,
) -> None:
    db = Database(tmp_path / "different-body-reconcile.db")
    now = datetime.now(UTC)
    # Meaningful text whitespace must not disappear during normalization:
    # "Bo dy" is different content from "Body", even though HTML indentation
    # and whitespace between tags may be normalized.
    different_body = {**ARTICLE, "content": "<section><p>Bo dy</p></section>"}
    client = _ScriptedClient(
        add_results=[httpx.ReadTimeout("relay response was lost")],
        draft_items=[
            _remote_draft(
                "different-body",
                update_time=now,
                article=different_body,
            )
        ],
    )

    with pytest.raises(DraftDeliveryNeedsReconcile):
        deliver_draft_once(
            db,
            client,  # type: ignore[arg-type]
            job_id=_job(db),
            account_id="account-1",
            articles=[ARTICLE],
            now=now,
        )

    assert client.calls["/cgi-bin/draft/add"] == 1
    assert _only_delivery(db)["status"] == "needs_reconcile"


@pytest.mark.parametrize("missing_field", ["update_time", "body"])
def test_incomplete_remote_evidence_never_releases_explicit_retry(
    tmp_path,
    missing_field: str,
) -> None:
    db = Database(tmp_path / f"untrusted-{missing_field}.db")
    started_at = datetime.now(UTC)
    remote = _remote_draft("untrusted-draft", update_time=started_at)
    if missing_field == "update_time":
        remote.pop("update_time")
    else:
        remote["content"]["news_item"][0].pop("content")
    client = _ScriptedClient(
        add_results=[
            httpx.ReadTimeout("relay response was lost"),
            "must-not-be-submitted",
        ],
        draft_items=[remote],
    )
    job_id = _job(db)

    with pytest.raises(DraftDeliveryNeedsReconcile):
        deliver_draft_once(
            db,
            client,  # type: ignore[arg-type]
            job_id=job_id,
            account_id="account-1",
            articles=[ARTICLE],
            now=started_at,
        )

    with pytest.raises(DraftDeliveryNeedsReconcile):
        deliver_draft_once(
            db,
            client,  # type: ignore[arg-type]
            job_id=job_id,
            account_id="account-1",
            articles=[ARTICLE],
            now=started_at + timedelta(minutes=2, seconds=1),
        )

    assert client.calls["/cgi-bin/draft/add"] == 1
    assert client.calls["/cgi-bin/draft/batchget"] == 2
    assert _only_delivery(db)["status"] == "needs_reconcile"


@pytest.mark.parametrize("short_first_page", [False, True])
def test_contradictory_pagination_never_releases_explicit_retry(
    tmp_path,
    short_first_page: bool,
) -> None:
    started_at = datetime.now(UTC)

    class ContradictoryPaginationClient(_ScriptedClient):
        def request(
            self,
            method: str,
            path: str,
            **kwargs: Any,
        ) -> dict[str, Any]:
            if path != "/cgi-bin/draft/batchget":
                return super().request(method, path, **kwargs)
            self.calls[path] += 1
            payload = kwargs.get("json_body") or {}
            offset = max(0, int(payload.get("offset") or 0))
            items = (
                [
                    _remote_draft(
                        "unrelated",
                        update_time=started_at,
                        article={**ARTICLE, "title": "Unrelated"},
                    )
                ]
                if short_first_page and offset == 0
                else []
            )
            return {
                "total_count": 25,
                "item_count": len(items),
                "item": items,
            }

    db = Database(tmp_path / f"contradictory-page-{short_first_page}.db")
    client = ContradictoryPaginationClient(
        add_results=[
            httpx.ReadTimeout("relay response was lost"),
            "must-not-be-submitted",
        ]
    )
    job_id = _job(db)

    with pytest.raises(DraftDeliveryNeedsReconcile):
        deliver_draft_once(
            db,
            client,  # type: ignore[arg-type]
            job_id=job_id,
            account_id="account-1",
            articles=[ARTICLE],
            now=started_at,
        )
    with pytest.raises(DraftDeliveryNeedsReconcile):
        deliver_draft_once(
            db,
            client,  # type: ignore[arg-type]
            job_id=job_id,
            account_id="account-1",
            articles=[ARTICLE],
            now=started_at + timedelta(minutes=2, seconds=1),
        )

    assert client.calls["/cgi-bin/draft/add"] == 1
    assert client.calls["/cgi-bin/draft/batchget"] >= 2
    assert _only_delivery(db)["status"] == "needs_reconcile"


def test_uncertain_result_waits_then_only_explicit_retry_can_submit_again(
    tmp_path,
) -> None:
    db = Database(tmp_path / "safe-release.db")
    started_at = datetime.now(UTC)
    client = _ScriptedClient(
        add_results=[
            httpx.ReadTimeout("relay response was lost"),
            "draft-after-safe-wait",
        ],
        draft_items=[],
    )
    job_id = _job(db)

    with pytest.raises(DraftDeliveryNeedsReconcile):
        deliver_draft_once(
            db,
            client,  # type: ignore[arg-type]
            job_id=job_id,
            account_id="account-1",
            articles=[ARTICLE],
            now=started_at,
        )
    assert client.calls["/cgi-bin/draft/add"] == 1
    assert _only_delivery(db)["status"] == "needs_reconcile"

    with pytest.raises(DraftDeliveryNeedsReconcile):
        deliver_draft_once(
            db,
            client,  # type: ignore[arg-type]
            job_id=job_id,
            account_id="account-1",
            articles=[ARTICLE],
            now=started_at + timedelta(minutes=1),
        )
    assert client.calls["/cgi-bin/draft/add"] == 1

    media_id = deliver_draft_once(
        db,
        client,  # type: ignore[arg-type]
        job_id=job_id,
        account_id="account-1",
        articles=[ARTICLE],
        now=started_at + timedelta(minutes=2, seconds=1),
    )
    assert media_id == "draft-after-safe-wait"
    assert client.calls["/cgi-bin/draft/add"] == 2
    assert _only_delivery(db)["status"] == "succeeded"


def test_definitive_api_failure_is_retryable_without_reconcile_hold(
    tmp_path,
) -> None:
    db = Database(tmp_path / "definitive-failure.db")
    client = _ScriptedClient(
        add_results=[
            WeChatAPIError(40013, "invalid appid"),
            "draft-after-fix",
        ]
    )
    job_id = _job(db)

    with pytest.raises(WeChatAPIError):
        deliver_draft_once(
            db,
            client,  # type: ignore[arg-type]
            job_id=job_id,
            account_id="account-1",
            articles=[ARTICLE],
        )
    assert _only_delivery(db)["status"] == "failed"

    media_id = deliver_draft_once(
        db,
        client,  # type: ignore[arg-type]
        job_id=job_id,
        account_id="account-1",
        articles=[ARTICLE],
    )
    assert media_id == "draft-after-fix"
    assert client.calls["/cgi-bin/draft/add"] == 2
    assert client.calls["/cgi-bin/draft/batchget"] == 0
    assert _only_delivery(db)["attempts"] == 2


def _job(db: Database) -> int:
    return db.create_job(topic="delivery reliability test")


def _only_delivery(db: Database) -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM draft_deliveries").fetchone()
    assert row is not None
    return dict(row)
