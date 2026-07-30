from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from app.services.batch_contracts import public_job


def test_public_review_link_uses_the_configured_local_ui_port(
    monkeypatch,
) -> None:
    monkeypatch.setenv("WECHAT_PUBLISHER_UI_PORT", "18775")
    projected = public_job(
        {
            "id": 42,
            "status": "ready_for_review",
            "step": "inject",
            "meta": {
                "batch_id": "batch with spaces",
                "official_account_id": "account-1",
            },
        },
        include_content=False,
    )

    parsed = urlparse(str(projected["review_url"]))
    assert parsed.scheme == "http"
    assert parsed.netloc == "127.0.0.1:18775"
    assert parse_qs(parsed.query) == {
        "view": ["review"],
        "batch_id": ["batch with spaces"],
        "job_id": ["42"],
    }


def test_public_review_link_prefers_hosted_ui_url(monkeypatch) -> None:
    monkeypatch.setenv(
        "WECHAT_PUBLISHER_PUBLIC_UI_URL",
        "https://publisher.bluebloodlab.cn/",
    )
    projected = public_job(
        {
            "id": 43,
            "status": "ready_for_review",
            "step": "inject",
            "meta": {"batch_id": "batch-online"},
        },
        include_content=False,
    )

    parsed = urlparse(str(projected["review_url"]))
    assert parsed.scheme == "https"
    assert parsed.netloc == "publisher.bluebloodlab.cn"
    assert parse_qs(parsed.query) == {
        "view": ["review"],
        "batch_id": ["batch-online"],
        "job_id": ["43"],
    }


def test_failed_or_standalone_job_has_no_visual_review_link() -> None:
    failed = public_job(
        {
            "id": 7,
            "status": "failed",
            "step": "rewrite",
            "meta": {"batch_id": "batch-1"},
        },
        include_content=False,
    )
    standalone = public_job(
        {
            "id": 8,
            "status": "ready_for_review",
            "step": "inject",
            "meta": {},
        },
        include_content=False,
    )

    assert failed["review_url"] is None
    assert standalone["review_url"] is None


@pytest.mark.parametrize("status", ["injecting", "drafted", "published"])
def test_non_reviewable_terminal_or_inflight_job_has_no_review_link(
    status: str,
) -> None:
    projected = public_job(
        {
            "id": 9,
            "status": status,
            "step": "inject",
            "meta": {"batch_id": "batch-1"},
        },
        include_content=False,
    )

    assert projected["review_url"] is None
