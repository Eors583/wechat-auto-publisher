from __future__ import annotations

import os

from fastapi.testclient import TestClient

from app.api.server import create_api_app
from app.config import load_config
from app.feishu.runtime import get_runtime, update_runtime
from app.services.batches import BatchService
from app.services.onboarding import ONBOARDING_SETTING_KEY, OnboardingService


def _service(tmp_path, *, enabled: bool = False) -> tuple[dict, BatchService]:
    config = {
        **load_config(),
        "_db_path": str(tmp_path / "api-runtime-health.db"),
        "api": {"token": "runtime-test-token"},
        "feishu": {"enabled": enabled},
    }
    return config, BatchService(config)


def test_health_identifies_the_exact_process_and_launcher_session(
    tmp_path,
    monkeypatch,
) -> None:
    config, service = _service(tmp_path)
    monkeypatch.setenv(
        "WECHAT_PUBLISHER_LAUNCH_SESSION_ID",
        "health-session-123",
    )
    app = create_api_app(config, service, start_feishu=False)

    with TestClient(app) as client:
        payload = client.get("/health").json()

    assert payload["pid"] == os.getpid()
    assert payload["launcher_session_id"] == "health-session-123"
    assert payload["instance_root"] == config["_root"]


def test_legacy_global_feishu_runtime_is_not_used_by_multitenant_health(
    tmp_path,
) -> None:
    config, service = _service(tmp_path)
    service.db.set_setting(
        "feishu.runtime",
        '{"status":"running","last_error":"stale failure"}',
    )
    app = create_api_app(config, service, start_feishu=True)

    with TestClient(app) as client:
        payload = client.get("/health").json()
        assert payload["feishu_enabled"] is False
        assert payload["feishu_status"] == "disabled"

    assert payload["feishu_integrations"] == 0
    assert payload["feishu_enabled_integrations"] == 0
    # The dormant legacy value is preserved for recovery, but no longer owns
    # API startup or unauthenticated health output.
    assert get_runtime(service.db)["status"] == "running"


def test_global_config_cannot_enable_a_multitenant_feishu_robot(tmp_path) -> None:
    config, service = _service(tmp_path, enabled=True)
    update_runtime(
        service.db,
        status="error",
        last_error="Authorization: Bearer SECRET-RUNTIME-TOKEN",
    )
    runtime = get_runtime(service.db)
    assert "SECRET-RUNTIME-TOKEN" not in str(runtime)

    app = create_api_app(config, service, start_feishu=False)
    with TestClient(app) as client:
        payload = client.get("/health").json()

    assert payload["feishu_enabled"] is False
    assert payload["feishu_error"] is None
    assert payload["feishu_error_code"] is None
    assert "SECRET-RUNTIME-TOKEN" not in str(payload)


def test_onboarding_status_api_is_read_only_and_secret_free(tmp_path) -> None:
    config, service = _service(tmp_path)
    app = create_api_app(config, service, start_feishu=False)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/onboarding/status",
            headers={"Authorization": "Bearer runtime-test-token"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["writer_ready"] is False
    assert payload["content_ready"] is False
    assert payload["draft_ready"] is False
    assert payload["current_step"] == "welcome"
    assert "api_key" not in response.text.casefold()
    assert "app_secret" not in response.text.casefold()
    assert "password" not in response.text.casefold()
    assert service.db.get_setting(ONBOARDING_SETTING_KEY) is None


def test_api_lifespan_runs_explicit_legacy_migration(
    tmp_path,
    monkeypatch,
) -> None:
    config, service = _service(tmp_path)
    calls: list[str] = []

    def migrate(instance: OnboardingService) -> dict:
        calls.append(str(instance.db.path))
        return {"migrated": False}

    monkeypatch.setattr(
        OnboardingService,
        "migrate_legacy_state",
        migrate,
    )
    app = create_api_app(config, service, start_feishu=False)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert calls == [str(service.db.path)]
