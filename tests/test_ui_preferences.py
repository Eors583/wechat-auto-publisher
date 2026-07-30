from app.ui.state import AppState


def test_target_accounts_are_remembered_and_filtered(tmp_path, monkeypatch) -> None:
    state = AppState.__new__(AppState)

    from app.db import Database

    state.db = Database(tmp_path / "preferences.db")
    monkeypatch.setattr(
        state,
        "account_options",
        lambda: {"account-a": "公众号A", "account-b": "公众号B"},
    )

    state.remember_account_ids(["account-b", "missing", "account-a"])

    assert state.remembered_account_ids() == ["account-b", "account-a"]


def test_custom_account_option_refreshers_run_with_account_changes() -> None:
    state = AppState.__new__(AppState)
    state.account_selects = []
    state.account_option_refreshers = []
    calls: list[str] = []

    state.register_account_option_refresher(lambda: calls.append("feishu"))
    state.refresh_account_selects()

    assert calls == ["feishu"]


def test_desktop_state_startup_runs_explicit_legacy_migration(
    tmp_path,
    monkeypatch,
) -> None:
    config = {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "desktop-startup.db"),
        "ai": {},
    }
    calls: list[str] = []
    monkeypatch.setattr("app.ui.state.load_config", lambda: config)
    monkeypatch.setattr(
        "app.ui.state.OnboardingService.migrate_legacy_state",
        lambda service: calls.append(str(service.db.path))
        or {"migrated": False},
    )

    state = AppState()

    assert calls == [str(state.db.path)]
