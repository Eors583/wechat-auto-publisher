from __future__ import annotations

import pytest

from app import accounts
from app.admin import server as admin_server
from app.db import Database
from app.services.auth import AuthService
from app.ui import state as state_module
from app.ui.state import AppState


def _legacy_config(tmp_path) -> dict[str, object]:
    database_path = tmp_path / "second-admin.db"
    return {
        "_root": str(tmp_path),
        "_db_path": str(database_path),
        "_db_target": str(database_path),
        "_database_url": "",
        "_data_dir": str(tmp_path / "data"),
        "ai": {},
        "wechat": {
            "app_id": "wx-legacy-owner",
            "app_secret": "legacy-owner-secret",
            "account_name": "历史公众号",
        },
        "benchmark": {},
    }


def _second_admin_state(tmp_path, monkeypatch) -> tuple[AppState, Database, dict]:
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "test-only-second-admin-owner-scope-key",
    )
    config = _legacy_config(tmp_path)
    root = Database(str(config["_db_target"]))
    auth = AuthService(root)
    default_admin = auth.ensure_default_admin()
    accounts.ensure_config_accounts_imported(
        root.for_user(str(default_admin["id"])), config
    )
    second_admin = root.create_user(
        username="second_admin",
        password_hash="test-hash",
        role="admin",
    )
    state = object.__new__(AppState)
    state.config = config
    state.db = root.for_user(str(second_admin["id"]))
    state.auth = AuthService(state.db)
    state.current_user = dict(second_admin)
    monkeypatch.setattr(state_module, "load_config", lambda: config)
    return state, root, dict(default_admin)


def test_default_admin_remains_owner_of_legacy_config_account(
    tmp_path, monkeypatch
) -> None:
    state, root, default_admin = _second_admin_state(tmp_path, monkeypatch)

    state.reload_config()

    account = root.for_user(str(default_admin["id"])).get_official_account(
        accounts.IMPORTED_DEFAULT_ACCOUNT_ID
    )
    assert account is not None
    assert account["owner_user_id"] == default_admin["id"]


def test_second_admin_reload_succeeds_without_importing_legacy_account(
    tmp_path, monkeypatch
) -> None:
    state, _root, _default_admin = _second_admin_state(tmp_path, monkeypatch)

    reloaded = state.reload_config()

    assert reloaded is state.config
    assert state.db.get_official_account(accounts.IMPORTED_DEFAULT_ACCOUNT_ID) is None
    assert state.db.list_official_accounts() == []


def test_second_admin_cannot_read_update_or_delete_legacy_account(
    tmp_path, monkeypatch
) -> None:
    state, root, default_admin = _second_admin_state(tmp_path, monkeypatch)
    account_id = accounts.IMPORTED_DEFAULT_ACCOUNT_ID

    assert state.db.get_official_account(account_id) is None
    with pytest.raises(ValueError, match="不属于当前登录账号"):
        state.db.upsert_official_account(
            {
                "id": account_id,
                "name": "越权修改",
                "app_id": "wx-hijack",
                "app_secret_encrypted": "encrypted-hijack",
                "model_id": "",
                "enabled": True,
            }
        )
    state.db.delete_official_account(account_id)
    preserved = root.for_user(str(default_admin["id"])).get_official_account(
        account_id
    )
    assert preserved is not None
    assert preserved["name"] == "历史公众号"


def test_non_admin_is_redirected_to_admin_login(monkeypatch) -> None:
    events: list[str] = []

    class FakeState:
        def __init__(self, *, recover_stale_work: bool) -> None:
            assert recover_stale_work is False
            self.auth = object()
            self.current_user = None

        def bind_user(self, user: dict) -> None:
            self.current_user = dict(user)

    monkeypatch.setattr(admin_server, "AppState", FakeState)
    monkeypatch.setattr(
        admin_server,
        "current_desktop_user",
        lambda _auth: {"id": "user-a", "username": "user-a", "role": "user"},
    )
    monkeypatch.setattr(admin_server.ui, "add_css", lambda _css: None)
    monkeypatch.setattr(
        admin_server, "_logout", lambda _state: events.append("logout")
    )
    monkeypatch.setattr(
        admin_server,
        "_build_admin_login",
        lambda _state: events.append("login"),
    )

    admin_server.create_admin_app()

    assert events == ["logout", "login"]
