from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.admin import server as admin_server
from app.services.auth import SESSION_DAYS
from app.ui import desktop
from app.ui import server as ui_server
from app.ui.auth_persistence import auth_session_middleware_kwargs


def test_auth_cookie_lives_as_long_as_database_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTH_SESSION_COOKIE_SECURE", raising=False)

    assert auth_session_middleware_kwargs() == {
        "max_age": SESSION_DAYS * 24 * 60 * 60,
        "same_site": "lax",
        "https_only": False,
    }


def test_auth_cookie_can_be_restricted_to_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_SESSION_COOKIE_SECURE", "true")

    assert auth_session_middleware_kwargs()["https_only"] is True


@pytest.mark.parametrize(
    "module",
    [ui_server, desktop, admin_server],
    ids=["web-ui", "desktop", "admin"],
)
def test_ui_entrypoints_enable_persistent_auth_cookie(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
) -> None:
    received: list[dict[str, Any]] = []
    monkeypatch.setattr(module, "load_config", dict)
    monkeypatch.setattr(module, "database_target", lambda _config: "database")
    monkeypatch.setattr(module.ui, "run", lambda **kwargs: received.append(kwargs))

    module.main()

    assert len(received) == 1
    assert received[0]["session_middleware_kwargs"]["max_age"] == (
        SESSION_DAYS * 24 * 60 * 60
    )
    assert received[0]["session_middleware_kwargs"]["same_site"] == "lax"


def test_production_nicegui_user_storage_uses_persistent_volume() -> None:
    compose = (
        Path(__file__).resolve().parents[1] / "compose.production.yaml"
    ).read_text(encoding="utf-8")

    assert "NICEGUI_STORAGE_PATH: /app/data/nicegui/web" in compose
    assert "NICEGUI_STORAGE_PATH: /app/data/nicegui/admin" in compose
    assert "- app_data:/app/data" in compose
