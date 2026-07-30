from __future__ import annotations

import hashlib
import importlib
from typing import Any

from fastapi.testclient import TestClient

from app.api.server import create_api_app
from app.config import load_config
from app.db import Database
from app.feishu.presenter import format_status
from app.services.batch_contracts import public_job
from app.services.batches import BatchService
from app.services.failures import sanitize_failure_payload

preflight_module = importlib.import_module("app.services.preflight")


def _api_config(tmp_path) -> dict[str, Any]:
    return {**load_config(), "_db_path": str(tmp_path / "api-failure.db")}


def test_retry_api_keeps_detail_and_returns_structured_sanitized_failure(
    tmp_path,
    monkeypatch,
) -> None:
    config = _api_config(tmp_path)
    service = BatchService(config)

    def fail_retry(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ValueError(
            "HTTP 429 rate limit api_key=do-not-return "
            "Authorization: Bearer also-secret"
        )

    monkeypatch.setattr(service, "retry_job", fail_retry)
    client = TestClient(create_api_app(config, service, start_feishu=False))

    response = client.post(
        "/api/v1/batches/batch-1/jobs/7/retry",
        json={"step": "rewrite"},
    )

    assert response.status_code == 409
    payload = response.json()
    assert isinstance(payload["detail"], str)
    assert "do-not-return" not in response.text
    assert "also-secret" not in response.text
    failure = payload["failure"]
    assert {
        "code",
        "stage",
        "title",
        "reason",
        "impact",
        "recommendation",
        "retryable",
        "actions",
    } <= set(failure)
    assert failure["code"] == "rewrite.rate_limited"
    assert failure["stage"] == "rewrite"
    assert failure["retryable"] is True


def test_retry_api_forwards_inline_image_retry_target(
    tmp_path,
    monkeypatch,
) -> None:
    config = _api_config(tmp_path)
    service = BatchService(config)
    captured: dict[str, Any] = {}

    def retry(
        batch_id: str,
        job_id: int,
        **options: Any,
    ) -> dict[str, Any]:
        captured.update(
            {
                "batch_id": batch_id,
                "job_id": job_id,
                **options,
            }
        )
        return {"accepted": True}

    monkeypatch.setattr(service, "retry_job", retry)
    client = TestClient(create_api_app(config, service, start_feishu=False))

    response = client.post(
        "/api/v1/batches/batch-1/jobs/7/retry",
        json={
            "step": "inline_image",
            "image_index": 3,
            "image_id": "inline-image-3",
        },
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": True}
    assert captured == {
        "batch_id": "batch-1",
        "job_id": 7,
        "step": "inline_image",
        "model_id": None,
        "source_url": None,
        "raw_content": None,
        "image_index": 3,
        "image_id": "inline-image-3",
    }


def test_request_validation_error_also_uses_failure_envelope(tmp_path) -> None:
    config = _api_config(tmp_path)
    service = BatchService(config)
    client = TestClient(create_api_app(config, service, start_feishu=False))

    response = client.post("/api/v1/batches", json={"account_ids": []})

    assert response.status_code == 422
    payload = response.json()
    assert isinstance(payload["detail"], list)
    assert payload["failure"]["code"] == "batch.unknown"
    assert payload["failure"]["stage"] == "batch"


def test_structured_failure_payload_redacts_named_secret_fields() -> None:
    result = sanitize_failure_payload(
        {
            "message": "provider rejected request",
            "app_secret": "secret-one",
            "nested": {
                "accessToken": "secret-two",
                "safe": "token=secret-three",
            },
        }
    )

    assert result["app_secret"] == "***"
    assert result["nested"]["accessToken"] == "***"
    assert "secret-three" not in result["nested"]["safe"]


def test_public_job_failure_is_the_same_contract_presented_in_feishu() -> None:
    projected = public_job(
        {
            "id": 17,
            "status": "failed",
            "step": "inject",
            "account_id": "account-1",
            "account_name": "公众号一",
            "error": "invalid appsecret 40125 app_secret=never-show",
            "meta": {"batch_id": "batch-1"},
        },
        include_content=False,
    )

    failure = projected["failure"]
    message = format_status(
        {
            "id": "batch-1",
            "status": "failed",
            "progress": {"completed": 1, "total": 1},
            "jobs": [projected],
        }
    )

    assert failure["code"] == "inject.auth_invalid"
    assert failure["title"] in message
    assert failure["reason"] in message
    assert failure["recommendation"] in message
    assert "never-show" not in message


def _install_preflight_fakes(
    monkeypatch,
    tmp_path,
    *,
    template: dict[str, Any] | None = None,
) -> tuple[dict[str, int], dict[str, list[str]]]:
    config = {
        "_root": str(tmp_path),
        "editor_template": template or {"enabled": False},
        "inline_images": {"enabled": False},
    }
    monkeypatch.setattr(
        preflight_module,
        "apply_account_selection",
        lambda _cfg, _db, account_id: (
            config,
            {"id": account_id, "name": "公众号一", "model_id": ""},
        ),
    )
    counts = {"connection": 0, "cover": 0}
    library = {"ids": ["cover-1", "other-cover"]}

    def probe(_config: dict[str, Any], _db: Database) -> dict[str, Any]:
        counts["connection"] += 1
        return {
            "status": "healthy",
            "mode": "direct",
            "details": {
                "material": {"reachable": True, "total_count": 2},
                "draft": {"reachable": True, "total_count": 0},
            },
        }

    def batch_material(
        _client: Any,
        material_type: str = "image",
        offset: int = 0,
        count: int = 20,
    ) -> dict[str, Any]:
        assert material_type == "image"
        counts["cover"] += 1
        ids = list(library["ids"])
        rows = [
            {"media_id": media_id}
            for media_id in ids[offset : offset + count]
        ]
        return {"total_count": len(ids), "item": rows}

    class FakePipeline:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        @staticmethod
        def _wechat_client() -> object:
            return object()

    monkeypatch.setattr(preflight_module, "_probe_wechat_connection", probe)
    monkeypatch.setattr(
        preflight_module,
        "_wechat_connection_mode",
        lambda *_args, **_kwargs: "direct",
    )
    monkeypatch.setattr(preflight_module, "batch_get_material", batch_material)
    monkeypatch.setattr(preflight_module, "Pipeline", FakePipeline)
    return counts, library


def test_account_health_is_cached_but_job_cover_is_checked_every_time(
    tmp_path,
    monkeypatch,
) -> None:
    db = Database(tmp_path / "preflight-cover.db")
    counts, library = _install_preflight_fakes(monkeypatch, tmp_path)
    job = {
        "id": 8,
        "account_id": "account-1",
        "thumb_media_id": "cover-1",
        "html_content": "<p>正文</p>",
        "meta": {},
    }
    kwargs = {
        "jobs_by_account": {"account-1": [job]},
        "deep_model_check": False,
        "force_wechat_check": False,
    }

    first = preflight_module.preflight_accounts(
        db, ["account-1"], **kwargs
    )[0]
    library["ids"] = ["other-cover"]
    second = preflight_module.preflight_accounts(
        db, ["account-1"], **kwargs
    )[0]

    assert first["can_write"] is True
    assert second["can_write"] is False
    assert counts["connection"] == 1
    assert counts["cover"] == 2
    cover = next(item for item in second["checks"] if item["key"] == "cover")
    assert cover["ok"] is False
    assert "不属于该公众号" in cover["message"]


def test_job_template_hash_change_blocks_write_even_when_snapshot_exists(
    tmp_path,
    monkeypatch,
) -> None:
    template_path = tmp_path / "account-template.html"
    template_html = "<section><p>正文占位符</p><footer>固定页尾</footer></section>"
    template_path.write_text(template_html, encoding="utf-8")
    editor = {
        "enabled": True,
        "required": True,
        "snapshot_path": str(template_path),
        "placeholder": "正文占位符",
        "selected_media_id": "",
    }
    db = Database(tmp_path / "preflight-template.db")
    _install_preflight_fakes(monkeypatch, tmp_path, template=editor)
    job = {
        "id": 9,
        "account_id": "account-1",
        "thumb_media_id": "cover-1",
        "html_content": "<section><p>已审核正文</p></section>",
        "meta": {
            "editor_template_applied": True,
            "editor_template_sha256": hashlib.sha256(
                b"previous-template"
            ).hexdigest(),
        },
    }

    report = preflight_module.preflight_accounts(
        db,
        ["account-1"],
        jobs_by_account={"account-1": [job]},
    )[0]

    template_check = next(
        item for item in report["checks"] if item["key"] == "template"
    )
    assert report["can_write"] is False
    assert template_check["ok"] is False
    assert "审核后模板已发生变化" in template_check["message"]


def test_empty_material_is_not_reported_as_a_credential_or_draft_failure() -> None:
    checks = preflight_module._checks_from_wechat_health(
        {
            "status": "healthy",
            "details": {
                "material": {"reachable": True, "total_count": 0},
                "draft": {"reachable": True, "total_count": 3},
            },
        }
    )
    by_key = {str(item["key"]): item for item in checks}

    assert by_key["wechat"]["ok"] is True
    assert by_key["draft"]["ok"] is True
    assert by_key["material"]["ok"] is False
    assert "凭证有效" in by_key["wechat"]["message"]
    assert "没有封面图片素材" in by_key["material"]["message"]


def test_partial_endpoint_failure_preserves_reachable_draft_and_authentication() -> None:
    checks = preflight_module._checks_from_wechat_health(
        {
            "status": "unhealthy",
            "error": "素材接口暂时不可用",
            "details": {
                "material": {
                    "reachable": False,
                    "error": "素材接口暂时不可用",
                },
                "draft": {"reachable": True, "total_count": 2},
            },
        }
    )
    by_key = {str(item["key"]): item for item in checks}

    assert by_key["wechat"]["ok"] is True
    assert by_key["draft"]["ok"] is True
    assert by_key["material"]["ok"] is False
    assert "草稿接口正常" in by_key["draft"]["message"]
    assert "素材接口暂时不可用" in by_key["material"]["message"]


def test_cached_unknown_wechat_errors_are_sanitized_in_check_messages() -> None:
    checks = preflight_module._checks_from_wechat_health(
        {
            "status": "unhealthy",
            "error": (
                "unknown app_secret=wechat-private "
                "api_key=model-private access_token=token-private"
            ),
            "details": {},
        }
    )

    rendered = repr(checks)
    assert "wechat-private" not in rendered
    assert "model-private" not in rendered
    assert "token-private" not in rendered


def test_selected_template_is_remotely_checked_without_jobs_but_offline_is_local(
    tmp_path,
    monkeypatch,
) -> None:
    template_path = tmp_path / "selected-template.html"
    template_path.write_text(
        "<section><p>正文占位符</p><footer>固定页尾</footer></section>",
        encoding="utf-8",
    )
    editor = {
        "enabled": True,
        "required": True,
        "snapshot_path": str(template_path),
        "placeholder": "正文占位符",
        "selected_media_id": "template-draft-1",
        "selected_article_index": 0,
    }
    db = Database(tmp_path / "preflight-selected-template.db")
    _install_preflight_fakes(monkeypatch, tmp_path, template=editor)
    draft_reads: list[str] = []

    def get_draft(_client: Any, media_id: str) -> dict[str, Any]:
        draft_reads.append(media_id)
        return {
            "news_item": [
                {
                    "content": (
                        "<section><p>正文占位符</p>"
                        "<footer>固定页尾</footer></section>"
                    )
                }
            ]
        }

    monkeypatch.setattr(preflight_module, "get_draft", get_draft)

    refreshed = preflight_module.preflight_accounts(
        db,
        ["account-1"],
        force_wechat_check=True,
    )[0]
    refreshed_template = next(
        item for item in refreshed["checks"] if item["key"] == "template"
    )

    assert refreshed_template["ok"] is True
    assert draft_reads == ["template-draft-1"]

    def fail_if_remote(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("offline startup must not read the remote template")

    monkeypatch.setattr(preflight_module, "Pipeline", fail_if_remote)
    monkeypatch.setattr(preflight_module, "get_draft", fail_if_remote)

    offline = preflight_module.preflight_accounts(
        db,
        ["account-1"],
        allow_stale_wechat_cache=True,
    )[0]
    offline_template = next(
        item for item in offline["checks"] if item["key"] == "template"
    )

    assert offline_template["ok"] is True
