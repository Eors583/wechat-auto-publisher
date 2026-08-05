from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.api.server import create_api_app
from app.config import load_config
from app.services.batches import BatchService
from app.services.editorial_reviews import EditorialReviewConflict


def _api(
    tmp_path,
    *,
    token: str = "review-token",
    authenticated: bool = True,
) -> tuple[BatchService, TestClient]:
    config = {
        **load_config(),
        "_db_path": str(tmp_path / "editorial-review-api.db"),
        "api": {"token": token},
        "feishu": {"enabled": False},
    }
    service = BatchService(config)
    app = create_api_app(config, service, start_feishu=False)
    headers = (
        {"Authorization": f"Bearer {token}"}
        if authenticated and token
        else None
    )
    return service, TestClient(app, headers=headers)


def test_editorial_review_router_uses_existing_bearer_auth(
    tmp_path, monkeypatch
) -> None:
    service, client = _api(
        tmp_path,
        token="review-token",
        authenticated=False,
    )
    monkeypatch.setattr(
        service,
        "get_editorial_review_options",
        lambda: {"roles": [{"id": "chief_editor"}]},
    )

    with client:
        unauthorized = client.get("/api/v1/editorial-review/options")
        authorized = client.get(
            "/api/v1/editorial-review/options",
            headers={"Authorization": "Bearer review-token"},
        )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["roles"][0]["id"] == "chief_editor"


def test_editorial_review_profile_crud_contract(
    tmp_path, monkeypatch
) -> None:
    service, client = _api(tmp_path)
    calls: list[tuple[str, Any]] = []
    profiles = [
        {
            "id": "professional_depth",
            "name": "专业深度型",
            "builtin": True,
            "enabled": True,
            "config": {},
        },
        {
            "id": "custom-1",
            "name": "蓝血主编",
            "builtin": False,
            "enabled": True,
            "config": {"strictness": "strict"},
        },
    ]

    def list_profiles(*, include_builtin: bool = True) -> list[dict[str, Any]]:
        calls.append(("list", include_builtin))
        return profiles if include_builtin else profiles[1:]

    def save_profile(**payload: Any) -> dict[str, Any]:
        calls.append(("save", dict(payload)))
        return {
            "id": str(payload.get("profile_id") or "custom-new"),
            "name": payload["name"],
            "description": payload["description"],
            "builtin": False,
            "enabled": payload["enabled"],
            "config": payload["config"],
        }

    def delete_profile(profile_id: str) -> None:
        calls.append(("delete", profile_id))

    monkeypatch.setattr(
        service, "list_editorial_review_profiles", list_profiles
    )
    monkeypatch.setattr(
        service, "save_editorial_review_profile", save_profile
    )
    monkeypatch.setattr(
        service, "delete_editorial_review_profile", delete_profile
    )

    with client:
        listed = client.get(
            "/api/v1/editorial-review/profiles",
            params={"include_builtin": False},
        )
        fetched = client.get(
            "/api/v1/editorial-review/profiles/custom-1"
        )
        created = client.post(
            "/api/v1/editorial-review/profiles",
            json={
                "name": "新方案",
                "description": "说明",
                "config": {"role_ids": ["chief_editor"]},
                "enabled": True,
            },
        )
        updated = client.put(
            "/api/v1/editorial-review/profiles/custom-1",
            json={
                "name": "更新方案",
                "description": "",
                "config": {"strictness": "strict"},
                "enabled": False,
            },
        )
        deleted = client.delete(
            "/api/v1/editorial-review/profiles/custom-1"
        )
        missing = client.get(
            "/api/v1/editorial-review/profiles/missing"
        )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == ["custom-1"]
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "蓝血主编"
    assert created.status_code == 200
    assert created.json()["id"] == "custom-new"
    assert updated.status_code == 200
    assert updated.json()["id"] == "custom-1"
    assert updated.json()["enabled"] is False
    assert deleted.json() == {"id": "custom-1", "deleted": True}
    assert missing.status_code == 404
    assert ("delete", "custom-1") in calls


def test_account_editorial_review_default_contract(
    tmp_path, monkeypatch
) -> None:
    service, client = _api(tmp_path)
    captured: list[tuple[Any, ...]] = []

    def get_default(account_id: str) -> dict[str, Any]:
        captured.append(("get", account_id))
        return {
            "account_id": account_id,
            "profile_id": "professional_depth",
            "config": {"strictness": "standard"},
        }

    def set_default(
        account_id: str,
        *,
        profile_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        captured.append(("set", account_id, profile_id, config))
        return {
            "account_id": account_id,
            "profile_id": profile_id,
            "config": config,
        }

    monkeypatch.setattr(
        service, "get_account_editorial_review_default", get_default
    )
    monkeypatch.setattr(
        service, "set_account_editorial_review_default", set_default
    )

    with client:
        loaded = client.get(
            "/api/v1/accounts/account-a/editorial-review-default"
        )
        saved = client.put(
            "/api/v1/accounts/account-a/editorial-review-default",
            json={
                "profile_id": "custom-1",
                "config": {"strictness": "strict"},
            },
        )

    assert loaded.status_code == 200
    assert loaded.json()["profile_id"] == "professional_depth"
    assert saved.status_code == 200
    assert saved.json()["profile_id"] == "custom-1"
    assert captured[-1] == (
        "set",
        "account-a",
        "custom-1",
        {"strictness": "strict"},
    )


def test_editorial_review_and_application_lifecycle_contract(
    tmp_path, monkeypatch
) -> None:
    service, client = _api(tmp_path)
    captured: list[tuple[str, Any]] = []
    review = {
        "id": "review-1",
        "batch_id": "batch-1",
        "job_id": 12,
        "status": "completed",
        "result": {
            "issues": [
                {
                    "id": "issue-1",
                    "problem": "结论不够明确",
                    "can_auto_apply": True,
                }
            ]
        },
    }
    application = {
        "id": "application-1",
        "review_id": "review-1",
        "status": "candidate_ready",
        "candidate_snapshot": {"title": "新标题", "body": "新正文"},
    }

    def run_review(
        batch_id: str,
        job_id: int,
        *,
        profile_id: str | None,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        captured.append(
            ("run", (batch_id, job_id, profile_id, dict(config)))
        )
        return review

    def list_reviews(**filters: Any) -> list[dict[str, Any]]:
        captured.append(("list_reviews", dict(filters)))
        return [review]

    def generate_candidate(
        batch_id: str,
        job_id: int,
        review_id: str,
        **payload: Any,
    ) -> dict[str, Any]:
        captured.append(
            (
                "candidate",
                (batch_id, job_id, review_id, dict(payload)),
            )
        )
        return {**review, "status": "candidate_ready", "application": application}

    monkeypatch.setattr(service, "run_editorial_review", run_review)
    monkeypatch.setattr(
        service, "list_editorial_reviews", list_reviews
    )
    monkeypatch.setattr(
        service, "get_editorial_review", lambda review_id: review
    )
    monkeypatch.setattr(
        service,
        "generate_editorial_rewrite_candidate",
        generate_candidate,
    )
    monkeypatch.setattr(
        service,
        "list_editorial_review_applications",
        lambda review_id, *, limit=20: [application],
    )
    monkeypatch.setattr(
        service,
        "get_editorial_review_application",
        lambda application_id: application,
    )
    monkeypatch.setattr(
        service,
        "apply_editorial_review_application",
        lambda batch_id, job_id, application_id: {
            "id": job_id,
            "status": "ready_for_review",
            "review_status": "viewed",
        },
    )
    monkeypatch.setattr(
        service,
        "keep_editorial_review_source",
        lambda batch_id, job_id, application_id: {
            "id": job_id,
            "status": "ready_for_review",
            "review_status": "viewed",
            "version_choice": "source",
        },
    )

    def resolve_issue(
        review_id: str,
        issue_id: str,
        *,
        resolution: str,
        note: str,
        resolved_by: str,
    ) -> dict[str, Any]:
        captured.append(
            (
                "resolve",
                (review_id, issue_id, resolution, note, resolved_by),
            )
        )
        return {**review, "blocking_count": 0}

    monkeypatch.setattr(
        service, "resolve_editorial_review_issue", resolve_issue
    )

    with client:
        started = client.post(
            "/api/v1/batches/batch-1/jobs/12/editorial-reviews",
            json={
                "profile_id": "professional_depth",
                "config": {"strictness": "strict"},
            },
        )
        listed = client.get(
            "/api/v1/editorial-reviews",
            params={"batch_id": "batch-1", "job_id": 12, "limit": 10},
        )
        fetched = client.get("/api/v1/editorial-reviews/review-1")
        generated = client.post(
            "/api/v1/batches/batch-1/jobs/12/"
            "editorial-reviews/review-1/rewrite-candidates",
            json={
                "issue_ids": ["issue-1"],
                "rewrite_mode": "selected_issues",
                "paragraph_numbers": [2],
                "instruction": "结论提前",
            },
        )
        applications = client.get(
            "/api/v1/editorial-reviews/review-1/applications",
            params={"limit": 5},
        )
        fetched_application = client.get(
            "/api/v1/editorial-review-applications/application-1"
        )
        applied = client.post(
            "/api/v1/batches/batch-1/jobs/12/"
            "editorial-review-applications/application-1/apply"
        )
        source_kept = client.post(
            "/api/v1/batches/batch-1/jobs/12/"
            "editorial-review-applications/application-1/keep-source"
        )
        resolved = client.patch(
            "/api/v1/editorial-reviews/review-1/issues/issue-1",
            json={
                "resolution": "waived",
                "note": "运营人员已核实",
                "resolved_by": "operator-a",
            },
        )

    assert started.status_code == 200
    assert listed.status_code == 200
    assert fetched.status_code == 200
    assert generated.status_code == 200
    assert generated.json()["application"]["id"] == "application-1"
    assert applications.status_code == 200
    assert fetched_application.status_code == 200
    assert applied.status_code == 200
    assert applied.json()["review_status"] == "viewed"
    assert source_kept.status_code == 200
    assert source_kept.json()["version_choice"] == "source"
    assert resolved.status_code == 200
    assert resolved.json()["blocking_count"] == 0
    assert (
        "candidate",
        (
            "batch-1",
            12,
            "review-1",
            {
                "issue_ids": ["issue-1"],
                "rewrite_mode": "selected_issues",
                "paragraph_numbers": [2],
                "instruction": "结论提前",
            },
        ),
    ) in captured
    assert captured[-1] == (
        "resolve",
        (
            "review-1",
            "issue-1",
            "waived",
            "运营人员已核实",
            "operator-a",
        ),
    )


def test_editorial_review_api_maps_missing_and_conflict_errors(
    tmp_path, monkeypatch
) -> None:
    service, client = _api(tmp_path)

    def conflict(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise EditorialReviewConflict("文章已被修改")

    def missing(_review_id: str) -> dict[str, Any]:
        raise KeyError("AI 评审不存在")

    monkeypatch.setattr(service, "run_editorial_review", conflict)
    monkeypatch.setattr(service, "get_editorial_review", missing)

    with client:
        stale = client.post(
            "/api/v1/batches/batch-1/jobs/12/editorial-reviews",
            json={},
        )
        not_found = client.get(
            "/api/v1/editorial-reviews/missing"
        )
        invalid_resolution = client.patch(
            "/api/v1/editorial-reviews/review-1/issues/issue-1",
            json={"resolution": "ignored"},
        )
        invalid_paragraph = client.post(
            "/api/v1/batches/batch-1/jobs/12/"
            "editorial-reviews/review-1/rewrite-candidates",
            json={
                "rewrite_mode": "selected_paragraphs",
                "paragraph_numbers": [0],
            },
        )

    assert stale.status_code == 409
    assert "文章已被修改" in stale.json()["detail"]
    assert not_found.status_code == 404
    assert invalid_resolution.status_code == 422
    assert invalid_paragraph.status_code == 422
