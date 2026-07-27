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
