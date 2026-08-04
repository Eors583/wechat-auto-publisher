from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.server import create_api_app
from app.config import load_config
from app.db import Database
from app.pipeline import Pipeline
from app.services.batches import BatchService
from app.services.failures import classify_job_failure, sanitize_failure_text
from app.services.job_attempts import run_tracked_job_stage
from app.services.model_readiness import active_model_auth_failure_ids


def _config(tmp_path) -> dict[str, Any]:
    return {
        **load_config(),
        "_db_path": str(tmp_path / "p0.db"),
        "api": {"token": "p0-test-token"},
    }


def _api_headers() -> dict[str, str]:
    return {"Authorization": "Bearer p0-test-token"}


def _account(db: Database, account_id: str, *, priority: int = 0) -> None:
    db.upsert_official_account(
        {
            "id": account_id,
            "name": account_id,
            "app_id": f"wx-{account_id}",
            "app_secret_encrypted": "encrypted",
            "model_id": "",
            "review_priority": priority,
            "enabled": True,
        }
    )


def _batch_job(
    db: Database,
    *,
    batch_id: str,
    account_id: str,
    status: str,
    step: str,
    review_status: str = "unviewed",
    error: str | None = None,
    scheduled_at: str | None = None,
    requested_by: str | None = None,
    chat_id: str | None = None,
) -> int:
    db.create_batch(
        batch_id,
        topic=f"topic-{batch_id}",
        source_url="https://example.test/a",
        requested_by=requested_by,
        chat_id=chat_id,
    )
    job_id = db.create_job(
        topic=f"topic-{batch_id}",
        source="test",
        source_url="https://example.test/a",
        raw_content="raw",
        meta={
            "batch_id": batch_id,
            "official_account_id": account_id,
            "official_account_name": account_id,
        },
    )
    db.update_job(
        job_id,
        status=status,
        step=step,
        error=error,
        body="正文" * 20,
        selected_title=f"title-{batch_id}",
        html_content="<p>正文</p>",
        scheduled_at=scheduled_at,
    )
    db.attach_batch_job(batch_id, job_id, account_id, account_id)
    if review_status != "unviewed":
        db.update_batch_job_review(batch_id, job_id, review_status)
    return job_id


def test_review_inbox_counts_pagination_priority_and_failure(tmp_path) -> None:
    service = BatchService(_config(tmp_path))
    db = service.db
    _account(db, "normal")
    _account(db, "priority", priority=100)
    today_id = _batch_job(
        db,
        batch_id="today",
        account_id="normal",
        status="ready_for_review",
        step="inject",
    )
    overdue_id = _batch_job(
        db,
        batch_id="overdue",
        account_id="normal",
        status="ready_for_review",
        step="inject",
    )
    scheduled_id = _batch_job(
        db,
        batch_id="scheduled",
        account_id="normal",
        status="ready_for_review",
        step="inject",
        scheduled_at=datetime.now().astimezone().date().isoformat(),
    )
    priority_id = _batch_job(
        db,
        batch_id="priority",
        account_id="priority",
        status="ready_for_review",
        step="inject",
    )
    write_failed_id = _batch_job(
        db,
        batch_id="write-failed",
        account_id="normal",
        status="failed",
        step="inject",
        error="40125 invalid appsecret",
    )
    generation_failed_id = _batch_job(
        db,
        batch_id="generation-failed",
        account_id="normal",
        status="failed",
        step="ingest",
        error=(
            "Failed to extract article body from URL: "
            "https://m.baidu.com/s?word=test"
        ),
    )
    drafted_id = _batch_job(
        db,
        batch_id="drafted",
        account_id="normal",
        status="drafted",
        step="inject",
        review_status="confirmed",
    )
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(
        timespec="microseconds"
    )
    yesterday = (
        datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        - timedelta(minutes=1)
    ).astimezone(timezone.utc).isoformat(timespec="microseconds")
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET created_at = ? WHERE id = ?",
            (old, overdue_id),
        )
        conn.execute(
            "UPDATE jobs SET created_at = ? WHERE id IN (?, ?)",
            (yesterday, scheduled_id, priority_id),
        )

    first = service.list_review_inbox(limit=2)
    assert first["counts"] == {
        "review": 4,
        "write_failed": 1,
        "generation_failed": 1,
        "today_completed": 1,
    }
    assert [item["job_id"] for item in first["items"]] == [
        today_id,
        overdue_id,
    ]
    assert [item["priority_reason"] for item in first["items"]] == [
        "今天生成",
        "超过24小时未审核",
    ]
    assert first["items"][0]["recommended_action"] == "打开快速审核并确认文章"
    assert first["next_cursor"] == "2"
    second = service.list_review_inbox(limit=2, cursor=first["next_cursor"])
    assert [item["job_id"] for item in second["items"]] == [
        scheduled_id,
        priority_id,
    ]
    failure_page = service.list_review_inbox(bucket="generation_failed")
    assert failure_page["items"][0]["job_id"] == generation_failed_id
    assert failure_page["items"][0]["failure"]["code"] == "ingest.invalid_source_url"
    assert failure_page["items"][0]["recommended_action"] == (
        "替换真实文章链接或粘贴正文后，仅重试抓取"
    )
    assert service.list_review_inbox(bucket="write_failed")["items"][0][
        "job_id"
    ] == write_failed_id
    assert service.list_review_inbox(bucket="today_completed")["items"][0][
        "job_id"
    ] == drafted_id


def test_review_inbox_scopes_before_pagination_and_counting(tmp_path) -> None:
    service = BatchService(_config(tmp_path))
    db = service.db
    _account(db, "normal")
    for index in range(25):
        _batch_job(
            db,
            batch_id=f"other-{index:02d}",
            account_id="normal",
            status="ready_for_review",
            step="inject",
            requested_by="other-user",
            chat_id="other-chat",
        )
    own_job_id = _batch_job(
        db,
        batch_id="own",
        account_id="normal",
        status="ready_for_review",
        step="inject",
        requested_by="current-user",
        chat_id="current-chat",
    )

    result = service.list_review_inbox(
        limit=20,
        requested_by="current-user",
        chat_id="current-chat",
    )

    assert result["counts"]["review"] == 1
    assert [item["job_id"] for item in result["items"]] == [own_job_id]
    assert result["next_cursor"] is None


def test_review_inbox_only_treats_layout_errors_as_blockers(tmp_path) -> None:
    service = BatchService(_config(tmp_path))
    _account(service.db, "normal")
    job_id = _batch_job(
        service.db,
        batch_id="layout-blockers",
        account_id="normal",
        status="ready_for_review",
        step="inject",
    )
    job = service.db.get_job(job_id)
    meta = dict((job or {}).get("meta") or {})
    meta["layout_quality"] = {
        "errors": ["模板正文占位符仍有残留"],
        "warnings": ["存在一个较长段落"],
    }
    service.db.update_job(job_id, meta_json=meta)

    item = service.list_review_inbox()["items"][0]

    assert "模板正文占位符仍有残留" in item["blockers"]
    assert "存在一个较长段落" not in item["blockers"]


def test_failure_contract_redacts_secrets_and_marks_transient() -> None:
    failure = classify_job_failure(
        "HTTP 429 api_key=secret-value token=abc",
        step="rewrite",
        status="failed",
    )
    assert failure is not None
    assert failure["code"] == "rewrite.rate_limited"
    assert failure["transient"] is True
    assert "secret-value" not in failure["technical_summary"]
    assert "token=abc" not in failure["technical_summary"]


def test_api_sanitizes_domain_exception_details(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    service = BatchService(config)

    def fail_inbox(**_kwargs):
        raise ValueError("Authorization: Bearer SECRET-API-TOKEN")

    monkeypatch.setattr(service, "list_review_inbox", fail_inbox)
    client = TestClient(
        create_api_app(config, service, start_feishu=False)
    )

    response = client.get(
        "/api/v1/review-inbox",
        headers=_api_headers(),
    )

    assert response.status_code == 400
    assert "SECRET-API-TOKEN" not in response.text
    assert "***" in response.text


@pytest.mark.parametrize(
    ("raw", "secrets"),
    [
        ("Authorization: Bearer abc.def", ("abc.def",)),
        ("Proxy-Authorization: Basic YWJjOjEyMw==", ("YWJjOjEyMw==",)),
        ("App Secret：test-secret-fragment", ("test-secret-fragment",)),
        ("api key = sk-live-value", ("sk-live-value",)),
        (
            "https://example.test/cb?token=abc123&appsecret=xyz789",
            ("abc123", "xyz789"),
        ),
        ("Cookie: a=1; b=2", ("a=1", "b=2")),
        ("Set-Cookie: sid=secret; Path=/; HttpOnly", ("sid=secret",)),
        ('{"access_token":"access-value","refresh_token":"refresh-value"}', (
            "access-value",
            "refresh-value",
        )),
    ],
)
def test_failure_sanitizer_covers_headers_keys_and_query_strings(
    raw: str, secrets: tuple[str, ...]
) -> None:
    safe = sanitize_failure_text(raw)
    assert "***" in safe
    for secret in secrets:
        assert secret not in safe


@pytest.mark.parametrize("status", ["drafted", "published"])
def test_retry_job_rejects_delivered_terminal_statuses(
    tmp_path, status: str
) -> None:
    service = BatchService(_config(tmp_path))
    batch_id = f"terminal-{status}"
    job_id = _batch_job(
        service.db,
        batch_id=batch_id,
        account_id="account-a",
        status=status,
        step="inject",
        review_status="confirmed",
    )
    before = service.db.get_job(job_id)

    with pytest.raises(ValueError, match="不能原地重试"):
        service.retry_job(batch_id, job_id, step="rewrite")

    after = service.db.get_job(job_id)
    assert after is not None
    assert before is not None
    assert after["status"] == status
    assert after["body"] == before["body"]
    assert after["html_content"] == before["html_content"]
    assert after["draft_media_id"] == before["draft_media_id"]


def test_retry_job_rejects_nonfailed_review_article(tmp_path) -> None:
    service = BatchService(_config(tmp_path))
    batch_id = "already-reviewable"
    job_id = _batch_job(
        service.db,
        batch_id=batch_id,
        account_id="account-a",
        status="ready_for_review",
        step="inject",
    )

    with pytest.raises(ValueError, match="只有失败或已停止"):
        service.retry_job(batch_id, job_id, step="render")


@pytest.mark.parametrize(
    ("requested_step", "job_step"),
    [
        ("ingest", "ingest"),
        ("rewrite", "rewrite"),
        ("title", "title_optimize"),
        ("render", "render"),
    ],
)
def test_retry_job_resumes_in_place_and_resets_review(
    tmp_path, monkeypatch, requested_step: str, job_step: str
) -> None:
    service = BatchService(_config(tmp_path))
    batch_id = f"retry-{requested_step}"
    job_id = _batch_job(
        service.db,
        batch_id=batch_id,
        account_id="account-a",
        status="failed",
        step=job_step,
        review_status="confirmed",
    )
    rich_meta = {
        **dict(service.db.get_job(job_id).get("meta") or {}),
        "source_images": ["https://example.test/source.jpg"],
        "inline_images_resolved": True,
        "inline_images": [{"index": 1, "url": "https://example.test/inline.jpg"}],
        "inline_image_warnings": ["old warning"],
        "generated_cover_active": True,
        "generated_cover": {"media_id": "generated-cover"},
        "cover_image_warning": "old cover warning",
    }
    service.db.update_job(
        job_id,
        body="generated body",
        titles_json=["rewrite title"],
        subtitles_json=["rewrite subtitle"],
        title_candidates_json=["optimized title"],
        selected_title="selected title",
        selected_subtitle="selected subtitle",
        html_content="<section>rendered</section>",
        digest="generated digest",
        thumb_media_id="generated-cover",
        ad_id="ad-1",
        draft_media_id="draft-1",
        publish_id="publish-1",
        meta_json=rich_meta,
    )
    before = service.db.get_job(job_id)
    calls: list[str] = []
    observed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.services.batches.apply_account_selection",
        lambda config, *_args, **_kwargs: (config, {"id": "account-a"}),
    )

    class FakePipeline:
        def __init__(self, _config, db=None) -> None:
            self.db = db or service.db

        def run_job(self, selected_job_id: int, **kwargs) -> None:
            calls.append(str(kwargs["from_step"]))
            observed.append(self.db.get_job(selected_job_id))
            self.db.update_job(
                selected_job_id,
                status="ready_for_review",
                step="inject",
                error=None,
            )

    monkeypatch.setattr("app.services.batches.Pipeline", FakePipeline)
    result = service.retry_job(batch_id, job_id, step=requested_step)
    assert result["status"] == "accepted"
    _wait_for(
        lambda: service.db.get_job(job_id)["status"] == "ready_for_review"
    )
    refreshed = service.db.get_batch(batch_id)
    assert refreshed is not None
    job = refreshed["jobs"][0]
    assert calls == [job_step]
    assert job["status"] == "ready_for_review"
    assert job["review_status"] == "unviewed"
    assert job["raw_content"] == before["raw_content"]
    invalidated = observed[0]
    if requested_step in {"ingest", "rewrite"}:
        assert invalidated["body"] in {None, ""}
        assert invalidated["titles"] == []
        assert invalidated["subtitles"] == []
        assert invalidated["title_candidates"] == []
        assert invalidated["selected_title"] is None
        assert invalidated["selected_subtitle"] is None
        assert invalidated["html_content"] == ""
        assert invalidated["digest"] is None
        assert invalidated["draft_media_id"] is None
        assert invalidated["thumb_media_id"] is None
        assert invalidated["meta"]["inline_images"] == []
        assert invalidated["meta"]["inline_images_resolved"] is False
        assert "generated_cover" not in invalidated["meta"]
        assert invalidated["meta"]["generated_cover_active"] is False
        if requested_step == "ingest":
            assert "source_images" not in invalidated["meta"]
        else:
            assert invalidated["meta"]["source_images"] == before["meta"][
                "source_images"
            ]
        assert invalidated["ad_id"] == before["ad_id"]
        assert invalidated["publish_id"] == before["publish_id"]
    elif requested_step == "title":
        assert invalidated["body"] == before["body"]
        assert invalidated["titles"] == before["titles"]
        assert invalidated["subtitles"] == before["subtitles"]
        assert invalidated["title_candidates"] == []
        assert invalidated["selected_title"] is None
        assert invalidated["selected_subtitle"] is None
        assert invalidated["html_content"] == ""
        assert invalidated["digest"] == before["digest"]
        assert invalidated["draft_media_id"] == before["draft_media_id"]
        assert invalidated["meta"]["inline_images"] == before["meta"][
            "inline_images"
        ]
        assert invalidated["thumb_media_id"] is None
        assert "generated_cover" not in invalidated["meta"]
    else:
        assert invalidated["html_content"] == ""
        for key in (
            "body",
            "titles",
            "subtitles",
            "title_candidates",
            "selected_title",
            "selected_subtitle",
            "digest",
            "thumb_media_id",
            "draft_media_id",
            "publish_id",
            "ad_id",
        ):
            assert invalidated[key] == before[key]
        assert invalidated["meta"] == before["meta"]


def test_retry_inject_preserves_confirmation(tmp_path, monkeypatch) -> None:
    service = BatchService(_config(tmp_path))
    batch_id = "retry-inject"
    job_id = _batch_job(
        service.db,
        batch_id=batch_id,
        account_id="account-a",
        status="failed",
        step="inject",
        review_status="confirmed",
    )
    monkeypatch.setattr(
        "app.services.batches.apply_account_selection",
        lambda config, *_args, **_kwargs: (config, {"id": "account-a"}),
    )

    class FakePipeline:
        def __init__(self, _config, db=None) -> None:
            self.db = db or service.db

        def review_and_inject(self, selected_job_id: int) -> None:
            self.db.update_job(
                selected_job_id,
                status="drafted",
                step="inject",
                error=None,
                draft_media_id="media-1",
            )

    monkeypatch.setattr("app.services.batches.Pipeline", FakePipeline)
    service.retry_job(batch_id, job_id, step="inject")
    _wait_for(
        lambda: service.db.get_job(job_id)["status"] == "drafted"
    )
    raw = service.db.get_batch(batch_id)
    assert raw is not None
    assert raw["jobs"][0]["review_status"] == "confirmed"


def test_retry_job_compare_and_set_allows_only_one_cross_process_claim(
    tmp_path, monkeypatch
) -> None:
    config = _config(tmp_path)
    first = BatchService(config)
    second = BatchService(config)
    batch_id = "retry-race"
    job_id = _batch_job(
        first.db,
        batch_id=batch_id,
        account_id="account-a",
        status="failed",
        step="rewrite",
    )
    monkeypatch.setattr(
        "app.services.batches.apply_account_selection",
        lambda config, *_args, **_kwargs: (config, {"id": "account-a"}),
    )
    # Separate locks emulate independent application processes. The database
    # compare-and-set must remain the source of truth.
    monkeypatch.setattr(
        "app.services.batches._retry_guard",
        lambda _db_path, _job_id: threading.Lock(),
    )
    claim_barrier = threading.Barrier(2)
    for service in (first, second):
        original_claim = service.db.claim_job_for_retry

        def synchronized_claim(
            *args, _claim=original_claim, **kwargs
        ) -> bool:
            claim_barrier.wait(timeout=3)
            return _claim(*args, **kwargs)

        monkeypatch.setattr(
            service.db, "claim_job_for_retry", synchronized_claim
        )

    release_worker = threading.Event()
    worker_calls: list[int] = []

    class FakePipeline:
        def __init__(self, _config, db=None) -> None:
            self.db = db or first.db

        def run_job(self, selected_job_id: int, **_kwargs) -> None:
            worker_calls.append(selected_job_id)
            release_worker.wait(timeout=3)
            self.db.update_job(
                selected_job_id,
                status="ready_for_review",
                step="inject",
                error=None,
            )

    monkeypatch.setattr("app.services.batches.Pipeline", FakePipeline)
    accepted: list[dict[str, Any]] = []
    rejected: list[Exception] = []

    def submit(service: BatchService) -> None:
        try:
            accepted.append(
                service.retry_job(batch_id, job_id, step="rewrite")
            )
        except Exception as exc:  # noqa: BLE001
            rejected.append(exc)

    callers = [
        threading.Thread(target=submit, args=(service,))
        for service in (first, second)
    ]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=5)
    release_worker.set()
    _wait_for(
        lambda: first.db.get_job(job_id)["status"] == "ready_for_review"
    )

    assert len(accepted) == 1
    assert len(rejected) == 1
    assert "状态已变化" in str(rejected[0])
    assert worker_calls == [job_id]


def test_retry_job_thread_start_failure_rolls_claim_back_to_failed(
    tmp_path, monkeypatch
) -> None:
    service = BatchService(_config(tmp_path))
    batch_id = "retry-thread-start-failed"
    job_id = _batch_job(
        service.db,
        batch_id=batch_id,
        account_id="account-a",
        status="failed",
        step="render",
        review_status="confirmed",
    )
    monkeypatch.setattr(
        "app.services.batches.apply_account_selection",
        lambda config, *_args, **_kwargs: (config, {"id": "account-a"}),
    )

    def fail_to_start(_thread) -> None:
        raise RuntimeError("thread factory unavailable")

    monkeypatch.setattr(threading.Thread, "start", fail_to_start)
    with pytest.raises(RuntimeError, match="thread factory unavailable"):
        service.retry_job(batch_id, job_id, step="render")

    job = service.db.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert job["step"] == "render"
    assert "恢复任务启动失败" in str(job["error"])
    raw = service.db.get_batch(batch_id)
    assert raw is not None
    assert raw["jobs"][0]["review_status"] == "unviewed"


def test_retry_with_replacement_url_clears_stale_ingest_text(
    tmp_path, monkeypatch
) -> None:
    service = BatchService(_config(tmp_path))
    batch_id = "retry-replacement-url"
    job_id = _batch_job(
        service.db,
        batch_id=batch_id,
        account_id="account-a",
        status="failed",
        step="ingest",
    )
    observed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.services.batches.apply_account_selection",
        lambda config, *_args, **_kwargs: (config, {"id": "account-a"}),
    )

    class FakePipeline:
        def __init__(self, _config, db=None) -> None:
            self.db = db or service.db

        def run_job(self, selected_job_id: int, **_kwargs) -> None:
            observed.append(self.db.get_job(selected_job_id))
            self.db.update_job(
                selected_job_id,
                status="ready_for_review",
                step="inject",
            )

    monkeypatch.setattr("app.services.batches.Pipeline", FakePipeline)
    replacement = "https://mp.weixin.qq.com/s/replacement"
    service.retry_job(
        batch_id,
        job_id,
        step="ingest",
        source_url=replacement,
    )
    _wait_for(lambda: bool(observed))
    assert observed[0]["source_url"] == replacement
    assert observed[0]["raw_content"] in {None, ""}
    assert observed[0]["meta"]["source_mode"] == "link"


def test_retry_images_by_image_id_records_independent_attempt(
    tmp_path, monkeypatch
) -> None:
    service = BatchService(_config(tmp_path))
    batch_id = "retry-one-image"
    job_id = _batch_job(
        service.db,
        batch_id=batch_id,
        account_id="account-a",
        status="failed",
        step="images",
        review_status="confirmed",
    )
    job = service.db.get_job(job_id)
    meta = dict((job or {}).get("meta") or {})
    meta["inline_images"] = [
        {
            "index": 2,
            "image_id": "argument-image-2",
            "url": "https://example.test/old.jpg",
        }
    ]
    service.db.update_job(job_id, meta_json=meta)
    monkeypatch.setattr(
        "app.services.batches.apply_account_selection",
        lambda config, *_args, **_kwargs: (
            {**config, "inline_images": {"image_model_id": "image-model"}},
            {"id": "account-a"},
        ),
    )
    calls: list[tuple[int, str]] = []

    def fake_regenerate(
        selected_batch_id: str,
        selected_job_id: int,
        selected_image_index: int,
        *,
        instruction: str,
        _retry_owned: bool = False,
    ) -> dict[str, Any]:
        assert selected_batch_id == batch_id
        assert _retry_owned is True
        assert service.db.get_job(selected_job_id)["status"] == "rendering"
        calls.append((selected_image_index, instruction))
        result = run_tracked_job_stage(
            service.db,
            selected_job_id,
            "images",
            lambda: (
                service.db.update_job(
                    selected_job_id,
                    status="rendering",
                    step="images",
                    error=None,
                )
                or service.db.get_job(selected_job_id)
                or {}
            ),
            model_id="image-model",
        )
        service.db.update_job(
            selected_job_id,
            status="ready_for_review",
            step="inject",
            error=None,
        )
        return service.db.get_job(selected_job_id) or result

    monkeypatch.setattr(service, "regenerate_inline_image", fake_regenerate)

    accepted = service.retry_job(
        batch_id,
        job_id,
        step="images",
        image_id="argument-image-2",
    )
    assert accepted["image_index"] == 2
    _wait_for(
        lambda: service.db.get_job(job_id)["status"] == "ready_for_review"
    )

    assert calls and calls[0][0] == 2
    attempts = service.db.list_job_attempts(job_id)
    assert attempts[0]["stage"] == "images"
    assert attempts[0]["status"] == "succeeded"
    assert attempts[0]["model_id"] == "image-model"
    assert service.db.get_batch(batch_id)["jobs"][0]["review_status"] == "unviewed"


def test_retry_images_without_target_prefers_one_failed_image_or_all() -> None:
    one_failed = {
        "meta": {
            "inline_images": [
                {"index": 1, "url": "https://example.test/one.jpg"},
                {"index": 2, "url": "", "status": "failed"},
            ]
        }
    }
    all_healthy = {
        "meta": {
            "inline_images": [
                {"index": 1, "url": "https://example.test/one.jpg"},
                {"index": 2, "url": "https://example.test/two.jpg"},
            ]
        }
    }

    assert (
        BatchService._resolve_retry_image_index(
            one_failed,
            image_index=None,
            image_id=None,
        )
        == 2
    )
    assert (
        BatchService._resolve_retry_image_index(
            all_healthy,
            image_index=None,
            image_id=None,
        )
        is None
    )


def test_retry_images_rate_limit_sets_and_enforces_backoff(
    tmp_path, monkeypatch
) -> None:
    service = BatchService(_config(tmp_path))
    batch_id = "retry-image-rate-limit"
    job_id = _batch_job(
        service.db,
        batch_id=batch_id,
        account_id="account-a",
        status="failed",
        step="images",
    )
    job = service.db.get_job(job_id)
    meta = dict((job or {}).get("meta") or {})
    meta["inline_images"] = [
        {
            "index": 1,
            "image_id": "argument-image-1",
            "url": "https://example.test/old.jpg",
        }
    ]
    service.db.update_job(job_id, meta_json=meta)
    monkeypatch.setattr(
        "app.services.batches.apply_account_selection",
        lambda config, *_args, **_kwargs: (config, {"id": "account-a"}),
    )

    def fail_regenerate(*_args, **kwargs):
        assert kwargs["_retry_owned"] is True
        assert service.db.get_job(job_id)["status"] == "rendering"
        return run_tracked_job_stage(
            service.db,
            job_id,
            "images",
            lambda: (_ for _ in ()).throw(
                RuntimeError("HTTP 429 Requests rate limit exceeded")
            ),
        )

    monkeypatch.setattr(service, "regenerate_inline_image", fail_regenerate)
    service.retry_job(
        batch_id,
        job_id,
        step="images",
        image_index=1,
    )
    _wait_for(
        lambda: service.db.get_job(job_id)["status"] == "failed"
        and service.db.get_job(job_id)["step"] == "images"
    )
    _wait_for(
        lambda: bool(service.db.list_job_attempts(job_id))
        and service.db.list_job_attempts(job_id)[0]["status"] == "failed"
    )
    time.sleep(0.05)

    attempt = service.db.list_job_attempts(job_id)[0]
    assert attempt["stage"] == "images"
    assert attempt["error_code"] == "images.rate_limited"
    assert attempt["next_retry_at"]
    with pytest.raises(ValueError, match="冷却期"):
        service.retry_job(
            batch_id,
            job_id,
            step="images",
            image_index=1,
        )


def test_tracked_timeout_attempt_sets_next_retry_at(tmp_path) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    job_id = _batch_job(
        db,
        batch_id="timeout-attempt",
        account_id="account-a",
        status="rewriting",
        step="rewrite",
    )

    with pytest.raises(TimeoutError):
        run_tracked_job_stage(
            db,
            job_id,
            "rewrite",
            lambda: (_ for _ in ()).throw(TimeoutError("request timeout")),
        )

    attempt = db.list_job_attempts(job_id)[0]
    assert attempt["status"] == "failed"
    assert attempt["error_code"] == "rewrite.timeout"
    assert attempt["next_retry_at"]


def test_pipeline_can_record_render_work_as_images_attempt(
    tmp_path, monkeypatch
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    job_id = _batch_job(
        db,
        batch_id="image-attempt-override",
        account_id="account-a",
        status="rendering",
        step="images",
    )
    pipe = Pipeline(config, db=db)

    def fake_render(
        job: dict[str, Any],
        *,
        cover_media_id=None,
    ) -> dict[str, Any]:
        db.update_job(job_id, status="rendering", step="images")
        return db.get_job(job_id) or job

    monkeypatch.setattr(pipe, "_step_render", fake_render)
    pipe.run_job(
        job_id,
        review=True,
        from_step="render",
        attempt_stage_overrides={"render": "images"},
        attempt_model_ids={"images": "image-model"},
    )

    attempts = db.list_job_attempts(job_id)
    assert [(item["stage"], item["status"]) for item in attempts] == [
        ("images", "succeeded")
    ]
    assert attempts[0]["model_id"] == "image-model"


def test_pipeline_model_auth_failure_invalidates_current_model_readiness(
    tmp_path,
    monkeypatch,
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    model_id = "pipeline-text-model"
    db.upsert_ai_model(
        {
            "id": model_id,
            "name": "流水线文本模型",
            "provider_type": "openai_compatible",
            "api_base": "https://model.example.test/v1",
            "model": "chat",
            "api_key_encrypted": "encrypted-key-fingerprint",
            "enabled": True,
        }
    )
    job_id = _batch_job(
        db,
        batch_id="pipeline-auth-failure",
        account_id="account-a",
        status="failed",
        step="rewrite",
    )
    job = db.get_job(job_id) or {}
    meta = dict(job.get("meta") or {})
    meta["selected_model_id"] = model_id
    db.update_job(job_id, meta_json=meta)
    pipe = Pipeline(config, db=db)
    monkeypatch.setattr(
        pipe,
        "_step_rewrite",
        lambda _job: (_ for _ in ()).throw(
            RuntimeError("HTTP 403 permission denied")
        ),
    )

    with pytest.raises(RuntimeError, match="403"):
        pipe.run_job(job_id, review=True, from_step="rewrite")

    assert active_model_auth_failure_ids(db, config) == {model_id}


def test_pipeline_does_not_expose_ready_before_attempt_is_terminal(
    tmp_path, monkeypatch
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    job_id = _batch_job(
        db,
        batch_id="attempt-before-ready",
        account_id="account-a",
        status="rendering",
        step="images",
    )
    pipe = Pipeline(config, db=db)
    finish_entered = threading.Event()
    allow_finish = threading.Event()
    original_finish = db.finish_job_attempt

    def blocked_finish(*args, **kwargs):
        finish_entered.set()
        assert allow_finish.wait(timeout=3)
        return original_finish(*args, **kwargs)

    def fake_render(
        job: dict[str, Any],
        *,
        cover_media_id=None,
    ) -> dict[str, Any]:
        db.update_job(job_id, status="rendering", step="images")
        return db.get_job(job_id) or job

    monkeypatch.setattr(db, "finish_job_attempt", blocked_finish)
    monkeypatch.setattr(pipe, "_step_render", fake_render)
    errors: list[Exception] = []

    def run_pipeline() -> None:
        try:
            pipe.run_job(
                job_id,
                review=True,
                from_step="render",
                attempt_stage_overrides={"render": "images"},
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=run_pipeline)
    worker.start()
    assert finish_entered.wait(timeout=3)

    assert db.get_job(job_id)["status"] == "rendering"
    assert db.list_job_attempts(job_id)[0]["status"] == "running"

    allow_finish.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert errors == []
    assert db.list_job_attempts(job_id)[0]["status"] == "succeeded"
    assert db.get_job(job_id)["status"] == "ready_for_review"


def test_pipeline_tracks_each_stage_and_recovery_closes_stale_attempt(
    tmp_path, monkeypatch
) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])
    batch_id = "tracked-render"
    job_id = _batch_job(
        db,
        batch_id=batch_id,
        account_id="account-a",
        status="failed",
        step="render",
    )
    pipe = Pipeline(config, db=db)

    def fake_render(job: dict[str, Any], *, cover_media_id=None) -> dict[str, Any]:
        db.update_job(job_id, status="rendering", step="render")
        return db.get_job(job_id)

    monkeypatch.setattr(pipe, "_step_render", fake_render)
    pipe.run_job(job_id, review=True, from_step="render")
    attempts = db.list_job_attempts(job_id)
    assert [(item["stage"], item["status"]) for item in attempts] == [
        ("render", "succeeded")
    ]

    stale_job_id = _batch_job(
        db,
        batch_id="stale-attempt",
        account_id="account-a",
        status="rewriting",
        step="rewrite",
    )
    stale = db.create_job_attempt(
        batch_id="stale-attempt",
        job_id=stale_job_id,
        stage="rewrite",
    )
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(
        timespec="microseconds"
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET updated_at = ? WHERE id = ?", (old, stale_job_id)
        )
        conn.execute(
            """
            UPDATE job_attempts
            SET started_at = ?, heartbeat_at = ?
            WHERE id = ?
            """,
            (old, old, stale["id"]),
        )
    assert db.recover_stale_jobs(older_than_minutes=30) == 1
    assert db.list_job_attempts(stale_job_id)[0]["status"] == "cancelled"


def test_recovery_immediately_reclaims_other_launch_session_and_batch(
    tmp_path,
) -> None:
    path = tmp_path / "lease-recovery.db"
    previous = Database(path, owner_session_id="launch-previous")
    batch_id = "other-launch"
    job_id = _batch_job(
        previous,
        batch_id=batch_id,
        account_id="account-a",
        status="rewriting",
        step="rewrite",
    )
    attempt = previous.create_job_attempt(
        batch_id=batch_id,
        job_id=job_id,
        stage="rewrite",
    )
    assert attempt["owner_session_id"] == "launch-previous"
    assert attempt["heartbeat_at"]

    current = Database(path, owner_session_id="launch-current")
    assert current.recover_stale_jobs(older_than_minutes=30) == 1
    assert current.get_job(job_id)["status"] == "cancelled"
    assert current.list_job_attempts(job_id)[0]["status"] == "cancelled"
    batch = current.get_batch(batch_id)
    assert batch is not None
    assert batch["status"] == "cancelled"


def test_recovery_keeps_current_session_until_heartbeat_lease_expires(
    tmp_path,
) -> None:
    db = Database(
        tmp_path / "current-lease.db",
        owner_session_id="launch-current",
    )
    batch_id = "current-launch"
    job_id = _batch_job(
        db,
        batch_id=batch_id,
        account_id="account-a",
        status="rewriting",
        step="rewrite",
    )
    attempt = db.create_job_attempt(
        batch_id=batch_id,
        job_id=job_id,
        stage="rewrite",
    )

    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(
        timespec="microseconds"
    )
    with db.connect() as conn:
        # The job timestamp alone is no longer authoritative while a current
        # session owns a live attempt lease.
        conn.execute(
            "UPDATE jobs SET updated_at = ? WHERE id = ?",
            (old, job_id),
        )
    assert db.recover_stale_jobs(older_than_minutes=30) == 0
    assert db.get_job(job_id)["status"] == "rewriting"
    before_heartbeat = str(attempt["heartbeat_at"])
    time.sleep(0.001)
    assert db.heartbeat_job_attempt(int(attempt["id"])) is True
    refreshed_attempt = db.list_job_attempts(job_id)[0]
    assert str(refreshed_attempt["heartbeat_at"]) > before_heartbeat
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE job_attempts
            SET heartbeat_at = ?
            WHERE id = ?
            """,
            (old, attempt["id"]),
        )
    assert db.recover_stale_jobs(older_than_minutes=30) == 1
    assert db.get_job(job_id)["status"] == "cancelled"
    assert db.get_batch(batch_id)["status"] == "cancelled"


def test_p0_api_routes_delegate_to_shared_service(tmp_path) -> None:
    config = _config(tmp_path)
    db = Database(config["_db_path"])

    class FakeService:
        def __init__(self) -> None:
            self.db = db

        def list_accounts(self):
            return [{"id": "a", "name": "A", "model_name": ""}]

        def list_review_inbox(self, **kwargs):
            return {"items": [], "counts": {}, "next_cursor": None, **kwargs}

        def list_job_attempts(self, batch_id, job_id, *, limit=50):
            return [{"batch_id": batch_id, "job_id": job_id, "limit": limit}]

        def retry_job(self, batch_id, job_id, **kwargs):
            return {
                "batch_id": batch_id,
                "job_id": job_id,
                "status": "accepted",
                **kwargs,
            }

    app = create_api_app(config, FakeService(), start_feishu=False)
    with TestClient(app) as client:
        inbox = client.get(
            "/api/v1/review-inbox"
            "?bucket=write_failed&search=quarterly&limit=10",
            headers=_api_headers(),
        )
        attempts = client.get(
            "/api/v1/batches/b1/jobs/7/attempts",
            headers=_api_headers(),
        )
        retried = client.post(
            "/api/v1/batches/b1/jobs/7/retry",
            headers=_api_headers(),
            json={"step": "render"},
        )
        health = client.get(
            "/api/v1/wechat/connection-health",
            headers=_api_headers(),
        )
    assert inbox.status_code == 200
    assert inbox.json()["bucket"] == "write_failed"
    assert inbox.json()["search"] == "quarterly"
    assert attempts.status_code == 200
    assert attempts.json()[0]["job_id"] == 7
    assert retried.status_code == 202
    assert retried.json()["step"] == "render"
    assert health.status_code == 200
    assert health.json()["items"][0]["status"] == "unknown"


def _wait_for(predicate, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("background operation did not finish")
