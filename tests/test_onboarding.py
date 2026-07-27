from __future__ import annotations

from typing import Any

import pytest

from app.ai.model_registry import GEMINI, MANUS, OPENAI_COMPATIBLE
from app.db import Database
from app.feishu.runtime import update_runtime
from app.services.onboarding import (
    FEISHU_TOKEN_URL,
    TEXT_MODEL_PRESETS,
    OnboardingService,
)


class _JsonResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return dict(self._payload)


def _service(tmp_path) -> OnboardingService:
    db = Database(tmp_path / "onboarding.db")
    return OnboardingService(
        db,
        {
            "_root": str(tmp_path),
            "ai": {},
        },
    )


def _save_model(
    service: OnboardingService,
    *,
    api_key: str = "sk-onboarding-private",
    preset_id: str = "deepseek",
) -> dict[str, Any]:
    return service.save_text_model(
        preset_id=preset_id,
        api_key=api_key,
    )


def _mark_model_tested(
    service: OnboardingService,
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
) -> dict[str, Any]:
    monkeypatch.setattr(
        service.configuration,
        "test_model",
        lambda tested_id: {
            "model_id": tested_id,
            "ok": True,
            "message": "连接成功",
        },
    )
    return service.test_text_model(model_id)


def _save_account(
    service: OnboardingService,
    model_id: str,
    *,
    suffix: str,
) -> dict[str, Any]:
    return service.configuration.save_account(
        name=f"测试公众号{suffix}",
        app_id=f"wx-onboarding-{suffix}",
        app_secret=f"wechat-secret-{suffix}",
        model_id=model_id,
    )


def _assert_secrets_absent(value: Any, *secrets: str) -> None:
    rendered = repr(value)
    for secret in secrets:
        assert secret not in rendered


def test_text_model_presets_are_beginner_safe_and_complete(tmp_path) -> None:
    service = _service(tmp_path)
    presets = service.model_presets()
    by_id = {str(item["id"]): item for item in presets}

    assert set(by_id) == {
        "deepseek",
        "qwen",
        "moonshot",
        "zhipu",
        "gemini",
        "manus",
        "custom",
    }
    assert set(TEXT_MODEL_PRESETS) == set(by_id)

    for preset_id, preset in by_id.items():
        assert preset["label"]
        assert preset["key_hint"]
        assert preset["provider_type"] in {
            OPENAI_COMPATIBLE,
            GEMINI,
            MANUS,
        }
        if preset_id == "custom":
            assert preset["api_base"] == ""
            assert preset["default_model"] == ""
            assert preset["models"] == ()
        else:
            assert preset["default_model"] in preset["models"]
            if preset["provider_type"] != GEMINI:
                assert str(preset["api_base"]).startswith("https://")
            assert str(preset["key_url"]).startswith("https://")
            assert str(preset["docs_url"]).startswith("https://")


def test_save_and_test_text_model_never_returns_or_records_plaintext_key(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    api_key = "sk-super-private-onboarding-key"

    model = _save_model(service, api_key=api_key)
    model_id = str(model["id"])
    tested = _mark_model_tested(service, monkeypatch, model_id)
    readiness = service.readiness()

    assert tested == {
        "model_id": model_id,
        "ok": True,
        "message": "连接成功",
    }
    assert readiness["model_tested"] is True
    assert readiness["tested_model_ids"] == [model_id]
    _assert_secrets_absent(model, api_key)
    _assert_secrets_absent(tested, api_key)
    _assert_secrets_absent(readiness, api_key)
    _assert_secrets_absent(
        service.db.get_setting("onboarding.guide"),
        api_key,
    )
    stored = service.db.get_ai_model(model_id)
    assert stored is not None
    assert api_key not in str(stored["api_key_encrypted"])


def test_changing_model_configuration_invalidates_its_previous_test(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _save_model(service, preset_id="qwen")
    model_id = str(model["id"])
    _mark_model_tested(service, monkeypatch, model_id)
    assert service.readiness()["tested_model_ids"] == [model_id]

    updated = service.save_text_model(
        preset_id="qwen",
        api_key=None,
        model="qwen-max",
    )

    assert updated["id"] == model_id
    readiness = service.readiness()
    assert readiness["model_tested"] is False
    assert readiness["tested_model_ids"] == []


def test_binding_tested_model_to_every_enabled_account_makes_core_ready(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    tested_model = _save_model(service)
    tested_model_id = str(tested_model["id"])
    _mark_model_tested(service, monkeypatch, tested_model_id)

    other_model = service.save_text_model(
        preset_id="custom",
        api_key="sk-other-model",
        api_base="https://llm.example.test/v1",
        model="other-chat",
    )
    first = _save_account(service, str(other_model["id"]), suffix="one")
    second = _save_account(service, str(other_model["id"]), suffix="two")

    before = service.readiness()
    assert before["accounts_bound"] is True
    assert before["account_models_tested"] is False
    assert before["core_ready"] is False

    rebound = service.bind_model_to_accounts(
        tested_model_id,
        [str(first["id"]), str(second["id"]), str(first["id"])],
    )

    assert {item["model_id"] for item in rebound} == {tested_model_id}
    assert len(rebound) == 2
    readiness = service.readiness()
    assert readiness["account_count"] == 2
    assert readiness["bound_account_count"] == 2
    assert readiness["accounts_bound"] is True
    assert readiness["account_models_tested"] is True
    assert readiness["core_ready"] is True


def test_feishu_credential_request_success_failure_and_secret_rotation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _save_model(service)
    model_id = str(model["id"])
    _mark_model_tested(service, monkeypatch, model_id)
    account = _save_account(service, model_id, suffix="feishu")
    requests: list[dict[str, Any]] = []

    def failing_post(
        url: str,
        *,
        json: dict[str, Any],
        timeout: int,
    ) -> _JsonResponse:
        requests.append({"url": url, "json": dict(json), "timeout": timeout})
        return _JsonResponse(
            200,
            {"code": 10003, "msg": "invalid app secret"},
        )

    with pytest.raises(ValueError, match="飞书凭证验证失败.*invalid app secret"):
        service.test_feishu_credentials(
            app_id="cli_onboarding",
            app_secret="wrong-secret",
            post=failing_post,
        )
    assert service.readiness()["feishu_credentials_tested"] is False

    def successful_post(
        url: str,
        *,
        json: dict[str, Any],
        timeout: int,
    ) -> _JsonResponse:
        requests.append({"url": url, "json": dict(json), "timeout": timeout})
        return _JsonResponse(
            200,
            {
                "code": 0,
                "msg": "ok",
                "tenant_access_token": "tenant-token-must-not-leak",
            },
        )

    result = service.test_feishu_credentials(
        app_id="cli_onboarding",
        app_secret="first-secret",
        post=successful_post,
    )
    assert result == {
        "ok": True,
        "message": "App ID 和 App Secret 验证成功",
        "app_id": "cli_onboarding",
    }
    assert requests[-1] == {
        "url": FEISHU_TOKEN_URL,
        "json": {
            "app_id": "cli_onboarding",
            "app_secret": "first-secret",
        },
        "timeout": 15,
    }
    _assert_secrets_absent(
        result,
        "first-secret",
        "tenant-token-must-not-leak",
    )

    service.save_feishu(
        app_id="cli_onboarding",
        app_secret="first-secret",
        agent_model_id=model_id,
        default_account_ids=[str(account["id"])],
        allow_all=False,
    )
    assert service.readiness()["feishu_credentials_tested"] is True

    service.save_feishu(
        app_id="cli_onboarding",
        app_secret="rotated-secret",
        agent_model_id=model_id,
        default_account_ids=[str(account["id"])],
        allow_all=False,
    )
    readiness = service.readiness()
    assert readiness["feishu_credentials_tested"] is False
    _assert_secrets_absent(
        readiness,
        "first-secret",
        "rotated-secret",
        "wrong-secret",
        "tenant-token-must-not-leak",
    )


def test_saving_feishu_requires_currently_tested_model_and_default_account(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _save_model(service)
    model_id = str(model["id"])
    account = _save_account(service, model_id, suffix="required")

    with pytest.raises(ValueError, match="先在第 1 步测试"):
        service.save_feishu(
            app_id="cli_required",
            app_secret="feishu-secret",
            agent_model_id=model_id,
            default_account_ids=[str(account["id"])],
            allow_all=False,
        )

    _mark_model_tested(service, monkeypatch, model_id)
    with pytest.raises(ValueError, match="至少选择一个机器人默认使用的公众号"):
        service.save_feishu(
            app_id="cli_required",
            app_secret="feishu-secret",
            agent_model_id=model_id,
            default_account_ids=[],
            allow_all=False,
        )

    service.save_text_model(
        preset_id="deepseek",
        api_key=None,
        model="deepseek-v4-pro",
    )
    assert service.readiness()["model_tested"] is False
    with pytest.raises(ValueError, match="先在第 1 步测试"):
        service.save_feishu(
            app_id="cli_required",
            app_secret="feishu-secret",
            agent_model_id=model_id,
            default_account_ids=[str(account["id"])],
            allow_all=False,
        )

    _mark_model_tested(service, monkeypatch, model_id)
    saved = service.save_feishu(
        app_id="cli_required",
        app_secret="feishu-secret",
        agent_model_id=model_id,
        default_account_ids=[str(account["id"])],
        allow_all=False,
    )
    assert saved["enabled"] is True
    assert saved["default_account_ids"] == [account["id"]]
    assert saved["agent_model_id"] == model_id
    assert saved["has_app_secret"] is True
    _assert_secrets_absent(saved, "feishu-secret")


def test_feishu_ready_requires_current_runtime_and_real_message_round_trip(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model = _save_model(service)
    model_id = str(model["id"])
    _mark_model_tested(service, monkeypatch, model_id)
    account = _save_account(service, model_id, suffix="runtime")
    service.test_feishu_credentials(
        app_id="cli_runtime",
        app_secret="runtime-secret",
        post=lambda *_args, **_kwargs: _JsonResponse(
            200,
            {"code": 0, "msg": "ok"},
        ),
    )
    service.save_feishu(
        app_id="cli_runtime",
        app_secret="runtime-secret",
        agent_model_id=model_id,
        default_account_ids=[str(account["id"])],
        allow_all=False,
        allowed_open_ids=["ou_ready"],
    )

    update_runtime(
        service.db,
        status="running",
        app_id="cli_other",
        last_open_id="ou_ready",
        started_at="2026-07-24T08:00:00+00:00",
        last_message_at="2026-07-24T08:01:00+00:00",
        last_reply_at="2026-07-24T08:02:00+00:00",
    )
    assert service.readiness()["feishu_ready"] is False

    update_runtime(
        service.db,
        status="running",
        app_id="cli_runtime",
        last_open_id="ou_ready",
        started_at="2026-07-24T09:00:00+00:00",
        last_message_at="2026-07-24T08:59:58+00:00",
        last_reply_at="2026-07-24T08:59:59+00:00",
    )
    assert service.readiness()["feishu_ready"] is False

    update_runtime(
        service.db,
        status="running",
        app_id="cli_runtime",
        last_open_id="ou_ready",
        started_at="2026-07-24T09:00:00+00:00",
        last_message_at="2026-07-24T09:00:05+00:00",
        last_reply_at="2026-07-24T09:00:06+00:00",
    )
    readiness = service.readiness()
    assert readiness["feishu_runtime_status"] == "running"
    assert readiness["feishu_ready"] is True

    update_runtime(
        service.db,
        status="running",
        app_id="cli_runtime",
        started_at="2026-07-24T09:00:00+00:00",
        last_message_at="2026-07-24T09:01:00+00:00",
        last_reply_at="2026-07-24T09:01:01+00:00",
        last_open_id="ou_not_allowed",
    )
    assert service.readiness()["feishu_ready"] is False

    update_runtime(
        service.db,
        status="running",
        app_id="cli_runtime",
        last_open_id="ou_ready",
        started_at="2026-07-24T09:00:00+00:00",
        last_message_at="2026-07-24T09:02:00+00:00",
        last_reply_at="2026-07-24T09:01:59+00:00",
    )
    assert service.readiness()["feishu_ready"] is False

    update_runtime(
        service.db,
        status="running",
        app_id="cli_runtime",
        last_open_id="ou_ready",
        started_at="2026-07-24T10:00:00+00:00",
    )
    assert service.readiness()["feishu_ready"] is False


def test_readiness_never_exposes_model_or_feishu_plaintext_secrets(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    model_key = "sk-model-never-return"
    feishu_secret = "feishu-secret-never-return"
    model = _save_model(service, api_key=model_key)
    model_id = str(model["id"])
    _mark_model_tested(service, monkeypatch, model_id)
    account = _save_account(service, model_id, suffix="privacy")
    service.test_feishu_credentials(
        app_id="cli_privacy",
        app_secret=feishu_secret,
        post=lambda *_args, **_kwargs: _JsonResponse(
            200,
            {
                "code": 0,
                "tenant_access_token": "tenant-token-never-return",
            },
        ),
    )
    service.save_feishu(
        app_id="cli_privacy",
        app_secret=feishu_secret,
        agent_model_id=model_id,
        default_account_ids=[str(account["id"])],
        allow_all=False,
    )

    readiness = service.readiness()

    _assert_secrets_absent(
        readiness,
        model_key,
        feishu_secret,
        "tenant-token-never-return",
        "wechat-secret-privacy",
    )
