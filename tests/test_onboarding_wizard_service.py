from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.ai.model_registry import encrypt_api_key
from app.db import Database
from app.editorial_review import DEFAULT_REVIEW_SCHEME_ID
from app.layout_profiles import DEFAULT_LAYOUT
from app.services.creation_plans import (
    BUILTIN_DEFAULT_CREATION_PLAN_ID,
    CreationPlanService,
)
from app.services.model_readiness import (
    active_model_auth_failure_ids,
    mark_model_auth_failure,
    record_model_auth_failure_for_error,
)
from app.services.onboarding import (
    ONBOARDING_SETTING_KEY,
    ONBOARDING_WIZARD_VERSION,
    OnboardingService,
)


def _service(tmp_path) -> OnboardingService:
    db = Database(tmp_path / "onboarding-wizard.db")
    return OnboardingService(
        db,
        {
            "_root": str(tmp_path),
            "_db_path": str(db.path),
            "ai": {},
        },
    )


def _tested_model(
    service: OnboardingService,
    monkeypatch: pytest.MonkeyPatch,
    *,
    preset_id: str = "deepseek",
) -> dict[str, Any]:
    model = service.save_text_model(
        preset_id=preset_id,
        api_key=f"sk-{preset_id}-private",
    )
    monkeypatch.setattr(
        service.configuration,
        "test_model",
        lambda model_id: {
            "model_id": model_id,
            "ok": True,
            "message": "连接成功",
        },
    )
    service.test_text_model(str(model["id"]))
    return model


def _save_account(
    service: OnboardingService,
    *,
    name: str,
    app_id: str,
    model_id: str,
) -> dict[str, Any]:
    return service.configuration.save_account(
        name=name,
        app_id=app_id,
        app_secret=f"secret-{app_id}",
        model_id=model_id,
    )


def _store_wechat_health(
    service: OnboardingService,
    account_id: str,
    *,
    status: str = "healthy",
    expired: bool = False,
) -> None:
    now = datetime.now(UTC)
    service.db.upsert_wechat_connection_health(
        account_id,
        status=status,
        checked_at=(now - timedelta(minutes=10)).isoformat(),
        expires_at=(
            now - timedelta(minutes=5) if expired else now + timedelta(minutes=5)
        ).isoformat(),
        details={
            "material": {
                "reachable": status == "healthy",
                "total_count": 1,
            },
            "draft": {
                "reachable": status == "healthy",
                "total_count": 0,
            },
        },
        error=("微信连接失败" if status == "unhealthy" else None),
    )


def test_guide_is_versioned_resumable_and_drops_unknown_secrets(tmp_path) -> None:
    service = _service(tmp_path)
    service.db.set_setting(
        ONBOARDING_SETTING_KEY,
        json.dumps(
            {
                "wizard_version": 0,
                "mode": "full",
                "current_step": "ai",
                "completed_steps": ["welcome"],
                "api_key": "sk-must-not-survive",
                "app_secret": "wechat-must-not-survive",
            }
        ),
    )

    guide = service.save_progress(
        current_step="account",
        completed_steps=["welcome", "ai", "ai"],
        selected_model_id="model-1",
        selected_account_ids=["account-1", "account-1"],
        connection_mode="direct",
    )
    stored = service.db.get_setting(ONBOARDING_SETTING_KEY) or ""

    assert guide == {
        "wizard_version": ONBOARDING_WIZARD_VERSION,
        "mode": "full",
        "current_step": "account",
        "completed_steps": ["welcome", "ai"],
        "selected_model_id": "model-1",
        "selected_account_ids": ["account-1"],
        "connection_mode": "direct",
        "force_open": False,
        "completed_at": None,
        "updated_at": guide["updated_at"],
    }
    assert guide["updated_at"]
    assert "sk-must-not-survive" not in stored
    assert "wechat-must-not-survive" not in stored
    assert "api_key" not in stored
    assert "app_secret" not in stored


def test_status_routes_a_brand_new_install_to_welcome(tmp_path) -> None:
    service = _service(tmp_path)

    status = service.status()

    assert status["writer_ready"] is False
    assert status["content_ready"] is False
    assert status["draft_ready"] is False
    assert status["wizard_required"] is True
    assert status["entrypoint"] == "wizard"
    assert status["current_step"] == "welcome"
    assert status["repair_step"] == "ai"


def test_legacy_guide_connection_mode_matches_existing_direct_configuration(
    tmp_path,
) -> None:
    service = _service(tmp_path)
    model = service.configuration.save_model(
        name="旧库模型",
        provider_type="openai_compatible",
        api_base="https://legacy.example.test/v1",
        model="legacy-chat",
        api_key="sk-legacy",
    )
    _save_account(
        service,
        name="旧库直连公众号",
        app_id="wx-legacy-direct",
        model_id=str(model["id"]),
    )

    guide = service.guide()

    assert guide["connection_mode"] == "direct"
    assert service.db.get_setting(ONBOARDING_SETTING_KEY) is None


@pytest.mark.parametrize("record_model_id", [True, False])
def test_legacy_success_evidence_upgrades_without_writing_guide_or_calling_model(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    record_model_id: bool,
) -> None:
    service = _service(tmp_path)
    model = service.configuration.save_model(
        name="旧库文章模型",
        provider_type="openai_compatible",
        api_base="https://legacy-model.example.test/v1",
        model="legacy-chat",
        api_key="sk-legacy-private",
    )
    account = _save_account(
        service,
        name="旧库公众号",
        app_id="wx-legacy-ready",
        model_id=str(model["id"]),
    )
    service.configuration.save_account_layout(
        str(account["id"]),
        deepcopy(DEFAULT_LAYOUT),
    )
    meta = {"official_account_id": account["id"]}
    if record_model_id:
        meta["selected_model_id"] = model["id"]
    job_id = service.db.create_job(
        topic="旧库成功文章",
        raw_content="历史内容",
        meta=meta,
    )
    service.db.update_job(
        job_id,
        status="ready_for_review",
        body="历史生成正文",
        html_content="<p>历史生成正文</p>",
    )
    _store_wechat_health(service, str(account["id"]))
    assert service.db.get_setting(ONBOARDING_SETTING_KEY) is None

    class _LocalOnlyClient:
        def complete(self, _prompt: str) -> str:
            raise AssertionError("legacy upgrade must not call a model")

    monkeypatch.setattr(
        "app.services.preflight.build_text_client",
        lambda *_args, **_kwargs: _LocalOnlyClient(),
    )

    status = service.status()

    assert status["legacy_upgrade_detected"] is True
    assert status["legacy_trusted_model_ids"] == [model["id"]]
    assert status["fingerprint_tested_model_ids"] == []
    assert status["writer_ready"] is True
    assert status["content_ready"] is True
    assert status["draft_ready"] is True
    assert status["entrypoint"] == "workspace"
    assert service.db.get_setting(ONBOARDING_SETTING_KEY) is None
    assert "sk-legacy-private" not in repr(status)


def test_legacy_success_does_not_trust_a_model_bound_after_that_job(tmp_path) -> None:
    service = _service(tmp_path)
    old_model = service.configuration.save_model(
        name="旧模型",
        provider_type="openai_compatible",
        api_base="https://old-model.example.test/v1",
        model="old-chat",
        api_key="sk-old-private",
    )
    new_model = service.configuration.save_model(
        name="新模型",
        provider_type="openai_compatible",
        api_base="https://new-model.example.test/v1",
        model="new-chat",
        api_key="sk-new-private",
    )
    account = _save_account(
        service,
        name="历史公众号",
        app_id="wx-legacy-switched",
        model_id=str(old_model["id"]),
    )
    job_id = service.db.create_job(
        topic="旧模型生成的文章",
        raw_content="历史内容",
        meta={"official_account_id": account["id"]},
    )
    service.db.update_job(
        job_id,
        status="ready_for_review",
        body="历史生成正文",
    )
    service.configuration.bind_account_model(
        str(account["id"]),
        str(new_model["id"]),
    )

    status = service.status()

    assert status["legacy_upgrade_detected"] is False
    assert status["legacy_trusted_model_ids"] == []
    assert status["writer_ready"] is False
    assert status["content_ready"] is False
    assert service.db.get_setting(ONBOARDING_SETTING_KEY) is None


def test_legacy_empty_or_failed_history_is_not_trusted(tmp_path) -> None:
    empty = _service(tmp_path / "empty")
    empty_status = empty.status()
    assert empty_status["legacy_upgrade_detected"] is False
    assert empty_status["legacy_trusted_model_ids"] == []
    assert empty_status["writer_ready"] is False
    assert empty_status["current_step"] == "welcome"
    assert empty.db.get_setting(ONBOARDING_SETTING_KEY) is None

    failed = _service(tmp_path / "failed")
    model = failed.configuration.save_model(
        name="失败历史模型",
        provider_type="openai_compatible",
        api_base="https://failed-model.example.test/v1",
        model="failed-chat",
        api_key="sk-failed-private",
    )
    account = _save_account(
        failed,
        name="失败历史公众号",
        app_id="wx-legacy-failed",
        model_id=str(model["id"]),
    )
    job_id = failed.db.create_job(
        topic="失败历史文章",
        raw_content="失败内容",
        meta={
            "official_account_id": account["id"],
            "selected_model_id": model["id"],
        },
    )
    failed.db.update_job(job_id, status="failed", error="历史生成失败")
    _store_wechat_health(failed, str(account["id"]))

    failed_status = failed.status()

    assert failed_status["legacy_upgrade_detected"] is False
    assert failed_status["legacy_trusted_model_ids"] == []
    assert failed_status["writer_ready"] is False
    assert failed_status["content_ready"] is False
    assert failed_status["draft_ready"] is False
    assert failed.db.get_setting(ONBOARDING_SETTING_KEY) is None


def test_vendor_preset_allows_a_future_nonempty_model_name(tmp_path) -> None:
    service = _service(tmp_path)

    saved = service.save_text_model(
        preset_id="deepseek",
        api_key="sk-future-model",
        model="deepseek-future-preview",
    )

    assert saved["model"] == "deepseek-future-preview"


def test_vendor_preset_accepts_advanced_api_base_and_invalidates_old_test(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _tested_model(service, monkeypatch)
    assert service.status()["writer_ready"] is True

    updated = service.save_text_model(
        preset_id="deepseek",
        api_key=None,
        api_base="https://gateway.example.test/v1",
    )

    assert updated["id"] == model["id"]
    assert updated["api_base"] == "https://gateway.example.test/v1"
    assert service.status()["writer_ready"] is False


def test_legacy_config_model_is_not_promoted_into_merchant_model_pool(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Database(tmp_path / "legacy-config-model.db")
    config = {
        "_root": str(tmp_path),
        "_db_path": str(db.path),
        "ai": {
            "primary": "moonshot",
            "fallback": "moonshot",
            "moonshot": {
                "api_key": "sk-config-legacy-private",
                "api_base": "https://api.moonshot.cn/v1",
                "model": "moonshot-v1-8k",
            },
        },
    }
    service = OnboardingService(db, config)
    account_id = "account_config_legacy"
    db.upsert_official_account(
        {
            "id": account_id,
            "name": "配置模型旧公众号",
            "app_id": "wx-config-legacy",
            "app_secret_encrypted": encrypt_api_key("wechat-config-private"),
            "model_id": "config:moonshot",
            "layout": deepcopy(DEFAULT_LAYOUT),
            "enabled": True,
        }
    )
    job_id = db.create_job(
        topic="配置模型历史成功文章",
        raw_content="历史正文",
        meta={
            "official_account_id": account_id,
            "selected_model_id": "config:moonshot",
        },
    )
    db.update_job(
        job_id,
        status="ready_for_review",
        body="历史生成正文",
    )
    _store_wechat_health(service, account_id)
    monkeypatch.setattr(
        "app.services.preflight.load_config",
        lambda: config,
    )

    before = service.status()
    assert before["writer_ready"] is False
    assert db.get_setting(ONBOARDING_SETTING_KEY) is None

    migrated = service.migrate_legacy_state()
    first_raw = db.get_setting(ONBOARDING_SETTING_KEY)
    second = service.migrate_legacy_state()
    after = service.status()

    assert migrated == {
        "migrated": False,
        "already_initialized": False,
        "trusted_model_ids": [],
    }
    assert second["migrated"] is False
    assert second["already_initialized"] is False
    assert db.get_setting(ONBOARDING_SETTING_KEY) == first_raw
    assert after["writer_ready"] is False
    assert after["content_ready"] is False
    assert after["draft_ready"] is False
    assert after["entrypoint"] == "wizard"
    assert first_raw is None
    assert "sk-config-legacy-private" not in str(first_raw)
    assert "wechat-config-private" not in str(first_raw)

    config["ai"]["moonshot"]["api_key"] = "sk-config-rotated-private"
    changed = service.status()

    assert changed["writer_ready"] is False
    assert changed["content_ready"] is False
    assert changed["entrypoint"] == "wizard"
    assert "sk-config-rotated-private" not in repr(changed)


def test_legacy_migration_rejects_database_model_changed_after_success(
    tmp_path,
) -> None:
    service = _service(tmp_path)
    model = service.configuration.save_model(
        name="旧库模型",
        provider_type="openai_compatible",
        api_base="https://old.example.test/v1",
        model="old-chat",
        api_key="sk-old-private",
    )
    account = _save_account(
        service,
        name="旧库公众号",
        app_id="wx-old-changed-before-migration",
        model_id=str(model["id"]),
    )
    job_id = service.db.create_job(
        topic="旧配置成功文章",
        raw_content="历史正文",
        meta={
            "official_account_id": account["id"],
            "selected_model_id": model["id"],
        },
    )
    service.db.update_job(
        job_id,
        status="ready_for_review",
        body="历史生成正文",
    )
    service.configuration.save_model(
        model_id=str(model["id"]),
        name="升级前已修改的模型",
        provider_type="openai_compatible",
        api_base="https://changed.example.test/v1",
        model="changed-chat",
        api_key="sk-changed-private",
    )

    migration = service.migrate_legacy_state()

    assert migration == {
        "migrated": False,
        "already_initialized": False,
        "trusted_model_ids": [],
    }
    assert service.db.get_setting(ONBOARDING_SETTING_KEY) is None
    assert service.status()["writer_ready"] is False


def test_current_model_auth_failure_revokes_readiness_until_real_retest(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _tested_model(service, monkeypatch)
    model_id = str(model["id"])

    failure = mark_model_auth_failure(
        service.db,
        service.config,
        model_id,
        failed_at="2026-07-29T00:00:00+00:00",
    )
    failed_status = service.status()
    raw_state = service.db.get_setting(ONBOARDING_SETTING_KEY) or ""
    stored = json.loads(raw_state)

    assert failure is not None
    assert failed_status["writer_ready"] is False
    assert failed_status["model_auth_failed_model_ids"] == [model_id]
    assert active_model_auth_failure_ids(
        service.db,
        service.config,
    ) == {model_id}
    assert stored["model_auth_failures"][model_id] == failure
    assert set(failure) == {"model_fingerprint", "failed_at"}
    assert "sk-deepseek-private" not in raw_state

    service.test_text_model(model_id)
    recovered = service.status()
    recovered_state = json.loads(
        service.db.get_setting(ONBOARDING_SETTING_KEY) or "{}"
    )

    assert recovered["writer_ready"] is True
    assert recovered["model_auth_failed_model_ids"] == []
    assert model_id not in recovered_state["model_auth_failures"]


def test_model_auth_failure_is_scoped_to_the_current_configuration_fingerprint(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _tested_model(service, monkeypatch)
    model_id = str(model["id"])
    mark_model_auth_failure(service.db, service.config, model_id)

    rotated = service.save_text_model(
        preset_id="deepseek",
        api_key="sk-rotated-private",
    )
    status = service.status()

    assert rotated["id"] == model_id
    assert status["model_auth_failed_model_ids"] == []
    # The old successful probe is also fingerprint-scoped, so rotation still
    # requires one new real test before readiness can recover.
    assert status["writer_ready"] is False
    assert "sk-rotated-private" not in repr(status)


def test_explicit_auto_check_retests_models_and_returns_sanitized_failures(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = service.save_text_model(
        preset_id="deepseek",
        api_key="sk-auto-check-private",
    )

    def fail_model_test(_model_id: str) -> dict[str, Any]:
        raise RuntimeError(
            "provider returned 401 Authorization: Bearer sk-auto-check-private"
        )

    monkeypatch.setattr(
        service.configuration,
        "test_model",
        fail_model_test,
    )

    progress_events: list[dict[str, Any]] = []
    status = service.auto_check(
        refresh_wechat=False,
        on_progress=progress_events.append,
    )

    assert status["writer_ready"] is False
    assert status["model_auth_failed_model_ids"] == [model["id"]]
    assert status["model_retest_failed_count"] == 1
    assert status["model_retest_results"] == [
        {
            "model_id": model["id"],
            "ok": False,
            "message": (
                "文本模型的 API Key 无效或已失效，"
                "请从厂商控制台重新复制后再试。"
            ),
        }
    ]
    assert "sk-auto-check-private" not in repr(status)
    assert "Authorization" not in repr(status)
    assert progress_events[0]["key"] == "article_ai"
    assert progress_events[0]["state"] == "running"
    assert any(
        event["key"] == "article_ai" and event["state"] == "failed"
        for event in progress_events
    )
    assert "sk-auto-check-private" not in repr(progress_events)
    assert "Authorization" not in repr(progress_events)


def test_explicit_auto_check_transient_failure_overwrites_old_success(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _tested_model(service, monkeypatch)
    model_id = str(model["id"])
    assert service.status()["writer_ready"] is True

    def fail_model_test(_model_id: str) -> dict[str, Any]:
        raise RuntimeError(
            "429 rate limit api_key=sk-transient-private"
        )

    monkeypatch.setattr(
        service.configuration,
        "test_model",
        fail_model_test,
    )

    status = service.auto_check(refresh_wechat=False)
    stored = json.loads(
        service.db.get_setting(ONBOARDING_SETTING_KEY) or "{}"
    )
    failure = stored["model_tests"][model_id]

    assert status["writer_ready"] is False
    assert status["model_auth_failed_model_ids"] == []
    assert status["model_retest_failed_count"] == 1
    assert failure["ok"] is False
    assert failure["model_fingerprint"]
    assert failure["tested_at"]
    assert failure["message"] == (
        "文本模型请求过于频繁，请查看额度或稍后重试。"
    )
    assert "sk-transient-private" not in repr(status)
    assert "sk-transient-private" not in json.dumps(stored, ensure_ascii=False)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("provider returned 401"), True),
        (RuntimeError("微信错误 40164"), False),
        (RuntimeError("provider returned 429"), False),
    ],
)
def test_auth_failure_helper_only_records_exact_model_401_or_403(
    tmp_path,
    error: Exception,
    expected: bool,
) -> None:
    service = _service(tmp_path)
    model = service.configuration.save_model(
        name="认证测试模型",
        provider_type="openai_compatible",
        api_base="https://auth-model.example.test/v1",
        model="auth-chat",
        api_key="sk-auth-private",
    )

    recorded = record_model_auth_failure_for_error(
        service.db,
        service.config,
        str(model["id"]),
        error,
    )

    assert recorded is expected
    failures = active_model_auth_failure_ids(service.db, service.config)
    assert (str(model["id"]) in failures) is expected
    assert "sk-auth-private" not in (
        service.db.get_setting(ONBOARDING_SETTING_KEY) or ""
    )


def test_create_first_account_applies_only_clean_beginner_defaults(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _tested_model(service, monkeypatch)
    app_secret = "wechat-first-account-private"

    account = service.create_first_account(
        name="蓝血研究",
        app_id="wx-first-account",
        app_secret=app_secret,
        model_id=str(model["id"]),
    )

    account_id = str(account["id"])
    record = service.db.get_official_account(account_id)
    assert record is not None
    layout = json.loads(str(record["layout_json"]))
    assert layout == deepcopy(DEFAULT_LAYOUT)
    assert layout["editor_template"]["enabled"] is False
    assert layout["inline_images"]["enabled"] is False
    assert (
        service.db.get_account_creation_plan_default(account_id)["creation_plan_id"]
        == BUILTIN_DEFAULT_CREATION_PLAN_ID
    )
    assert (
        service.db.get_account_editorial_review_default(account_id)["profile_id"]
        == DEFAULT_REVIEW_SCHEME_ID
    )
    assert json.loads(service.db.get_setting("ui.last_target_account_ids") or "[]") == [
        account_id
    ]
    guide = service.guide()
    assert guide["current_step"] == "wechat"
    assert guide["completed_steps"] == ["ai", "account"]
    assert guide["selected_model_id"] == model["id"]
    assert guide["selected_account_ids"] == [account_id]
    assert app_secret not in repr(account)
    assert app_secret not in (service.db.get_setting(ONBOARDING_SETTING_KEY) or "")


def test_create_first_account_is_idempotent_for_its_wizard_account(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _tested_model(service, monkeypatch)
    first = service.create_first_account(
        name="首次名称",
        app_id="wx-idempotent",
        app_secret="first-secret",
        model_id=str(model["id"]),
    )
    retried = service.create_first_account(
        name="重试后的名称",
        app_id="wx-idempotent",
        app_secret="rotated-secret",
        model_id=str(model["id"]),
    )

    assert retried["id"] == first["id"]
    assert retried["name"] == "重试后的名称"
    assert len(service.db.list_official_accounts()) == 1
    assert "rotated-secret" not in repr(retried)
    assert "rotated-secret" not in (
        service.db.get_setting(ONBOARDING_SETTING_KEY) or ""
    )


def test_create_first_account_updates_its_wizard_account_when_app_id_changes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _tested_model(service, monkeypatch)
    first = service.create_first_account(
        name="首次名称",
        app_id="wx-invalid",
        app_secret="first-secret",
        model_id=str(model["id"]),
    )

    corrected = service.create_first_account(
        name="修正后的公众号",
        app_id="wx-corrected",
        app_secret="corrected-secret",
        model_id=str(model["id"]),
    )

    assert corrected["id"] == first["id"]
    assert corrected["app_id"] == "wx-corrected"
    assert corrected["name"] == "修正后的公众号"
    assert len(service.db.list_official_accounts()) == 1
    assert service.guide()["selected_account_ids"] == [first["id"]]
    assert "corrected-secret" not in repr(corrected)
    assert "corrected-secret" not in (
        service.db.get_setting(ONBOARDING_SETTING_KEY) or ""
    )


def test_create_first_account_does_not_reset_unmanaged_historical_account(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _tested_model(service, monkeypatch)
    historical = _save_account(
        service,
        name="历史公众号",
        app_id="wx-history",
        model_id=str(model["id"]),
    )
    custom_layout = deepcopy(DEFAULT_LAYOUT)
    custom_layout["body"]["color"] = "#123456"
    custom_layout["inline_images"]["enabled"] = False
    CreationPlanService(service.db, service.config).apply_to_account(
        str(historical["id"]),
        BUILTIN_DEFAULT_CREATION_PLAN_ID,
    )
    service.configuration.save_account_layout(
        str(historical["id"]),
        custom_layout,
    )
    before = deepcopy(service.db.get_official_account(str(historical["id"])))

    with pytest.raises(ValueError, match="历史公众号"):
        service.create_first_account(
            name="不应覆盖",
            app_id="wx-history",
            app_secret="new-secret",
            model_id=str(model["id"]),
        )

    assert service.db.get_official_account(str(historical["id"])) == before
    assert (
        service.db.get_account_creation_plan_default(str(historical["id"]))[
            "creation_plan_id"
        ]
        == BUILTIN_DEFAULT_CREATION_PLAN_ID
    )


def test_create_first_account_compensates_when_default_application_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _tested_model(service, monkeypatch)
    guide_before = service.db.get_setting(ONBOARDING_SETTING_KEY)
    service.db.set_setting("ui.last_target_account_ids", '["existing"]')

    def fail_apply(
        _self: CreationPlanService,
        _account_id: str,
        _plan_id: str,
    ) -> dict[str, Any]:
        raise RuntimeError("plan application failed")

    monkeypatch.setattr(
        CreationPlanService,
        "apply_to_account",
        fail_apply,
    )

    with pytest.raises(RuntimeError, match="plan application failed"):
        service.create_first_account(
            name="应回滚",
            app_id="wx-rollback",
            app_secret="rollback-private",
            model_id=str(model["id"]),
        )

    assert service.db.list_official_accounts() == []
    assert service.db.get_setting(ONBOARDING_SETTING_KEY) == guide_before
    assert service.db.get_setting("ui.last_target_account_ids") == '["existing"]'


def test_status_uses_any_ready_account_and_does_not_block_on_history(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    tested = _tested_model(service, monkeypatch)
    good = _save_account(
        service,
        name="可用公众号",
        app_id="wx-good",
        model_id=str(tested["id"]),
    )
    untested = service.save_text_model(
        preset_id="custom",
        api_key="sk-untested",
        api_base="https://models.example.test/v1",
        model="other-chat",
    )
    bad = _save_account(
        service,
        name="历史异常公众号",
        app_id="wx-bad",
        model_id=str(untested["id"]),
    )

    def fake_preflight(
        _db: Database,
        account_ids: list[str],
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        return [
            {
                "account_id": account_id,
                "account_name": (
                    "可用公众号" if account_id == good["id"] else "历史异常公众号"
                ),
                "model_id": (
                    tested["id"] if account_id == good["id"] else untested["id"]
                ),
                "can_generate": account_id == good["id"],
                "can_write": account_id == good["id"],
                "checks": [],
            }
            for account_id in account_ids
        ]

    monkeypatch.setattr(
        "app.services.onboarding.preflight_accounts",
        fake_preflight,
    )
    status = service.status(refresh_wechat=True)

    assert status["writer_ready"] is True
    assert status["content_ready"] is True
    assert status["draft_ready"] is True
    assert status["core_ready"] is True
    assert status["account_models_tested"] is False
    assert status["entrypoint"] == "workspace"
    assert status["current_step"] == "complete"
    assert status["content_ready_account_ids"] == [good["id"]]
    assert status["draft_ready_account_ids"] == [good["id"]]
    checks = {item["account_id"]: item for item in status["account_checks"]}
    assert checks[str(good["id"])]["draft_ready"] is True
    assert checks[str(bad["id"])]["draft_ready"] is False


def test_empty_material_keeps_content_ready_but_blocks_draft_ready(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _tested_model(service, monkeypatch)
    account = _save_account(
        service,
        name="缺封面公众号",
        app_id="wx-no-cover",
        model_id=str(model["id"]),
    )
    monkeypatch.setattr(
        "app.services.onboarding.preflight_accounts",
        lambda *_args, **_kwargs: [
            {
                "account_id": account["id"],
                "account_name": account["name"],
                "model_id": model["id"],
                "can_generate": True,
                "can_write": False,
                "checks": [
                    {
                        "key": "wechat",
                        "name": "素材接口",
                        "ok": False,
                        "message": "连接正常，但没有封面图片素材",
                    }
                ],
            }
        ],
    )

    status = service.status(refresh_wechat=True)

    assert status["writer_ready"] is True
    assert status["content_ready"] is True
    assert status["draft_ready"] is False
    assert status["core_ready"] is True
    assert status["entrypoint"] == "wizard"
    assert status["current_step"] == "wechat"
    assert status["repair_step"] == "wechat"
    assert status["account_checks"][0]["can_write"] is False
    rendered = repr(status)
    assert "secret-wx-no-cover" not in rendered
    assert "sk-deepseek-private" not in rendered


def test_expired_healthy_cache_keeps_completed_user_in_workspace_and_refreshes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _tested_model(service, monkeypatch)
    account = service.create_first_account(
        name="缓存过期公众号",
        app_id="wx-expired-healthy",
        app_secret="expired-private",
        model_id=str(model["id"]),
    )
    account_id = str(account["id"])
    _store_wechat_health(service, account_id)
    service.complete()
    _store_wechat_health(service, account_id, expired=True)

    def fail_if_networked(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("offline onboarding status attempted a network probe")

    monkeypatch.setattr(
        "app.services.preflight.get_or_probe_wechat_connection_health",
        fail_if_networked,
    )

    status = service.status()

    assert status["draft_ready"] is True
    assert status["last_known_draft_ready"] is True
    assert status["entrypoint"] == "workspace"
    assert status["current_step"] == "complete"
    assert status["wechat_refresh_needed"] is True
    assert status["wechat_refresh_account_ids"] == [account_id]

    # Stale remote health may be reused, but local template/image changes are
    # always recomputed before retaining draft readiness.
    record = service.db.get_official_account(account_id)
    assert record is not None
    layout = json.loads(str(record["layout_json"]))
    layout["inline_images"]["enabled"] = True
    layout["inline_images"]["source_mode"] = "generate"
    layout["inline_images"]["image_model_id"] = ""
    record["layout"] = layout
    service.db.upsert_official_account(record)

    locally_broken = service.status()
    assert locally_broken["content_ready"] is True
    assert locally_broken["draft_ready"] is False
    assert locally_broken["entrypoint"] == "wizard"
    assert locally_broken["current_step"] == "wechat"


def test_stale_or_unhealthy_cache_never_counts_as_draft_ready(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _tested_model(service, monkeypatch)
    account = service.create_first_account(
        name="明确异常公众号",
        app_id="wx-explicitly-unhealthy",
        app_secret="unhealthy-private",
        model_id=str(model["id"]),
    )
    account_id = str(account["id"])
    _store_wechat_health(service, account_id, expired=True)
    service.db.invalidate_wechat_connection_health(account_id)

    stale = service.status()

    assert stale["draft_ready"] is False
    assert stale["last_known_draft_ready"] is False
    assert stale["wechat_refresh_needed"] is True
    assert stale["wechat_refresh_account_ids"] == [account_id]

    _store_wechat_health(
        service,
        account_id,
        status="unhealthy",
        expired=False,
    )
    unhealthy = service.status()

    assert unhealthy["draft_ready"] is False
    assert unhealthy["last_known_draft_ready"] is False
    assert unhealthy["entrypoint"] == "wizard"
    assert unhealthy["current_step"] == "wechat"
    assert unhealthy["wechat_refresh_needed"] is False


def test_complete_and_restart_preserve_configuration_and_force_reopen(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _tested_model(service, monkeypatch)
    account = service.create_first_account(
        name="完成配置公众号",
        app_id="wx-complete",
        app_secret="complete-private",
        model_id=str(model["id"]),
    )
    monkeypatch.setattr(
        service,
        "status",
        lambda **_kwargs: {
            "writer_ready": True,
            "content_ready": True,
            "draft_ready": True,
        },
    )

    completed = service.complete()
    restarted = service.restart()

    assert completed["current_step"] == "complete"
    assert completed["force_open"] is False
    assert completed["completed_at"]
    assert restarted["current_step"] == "welcome"
    assert restarted["completed_steps"] == []
    assert restarted["force_open"] is True
    assert restarted["completed_at"] is None
    assert restarted["selected_model_id"] == model["id"]
    assert restarted["selected_account_ids"] == [account["id"]]
    assert len(service.db.list_official_accounts()) == 1
