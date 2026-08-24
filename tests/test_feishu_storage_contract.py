from __future__ import annotations

from typing import Any

from app.feishu.session import FeishuSessionStore


class _IntegrationOnlyDatabase:
    def __init__(self) -> None:
        self.session: dict[str, Any] = {"batch_id": None, "context": {}}
        self.calls: list[tuple[str, str, str]] = []

    def get_feishu_session(
        self, integration_id: str, chat_id: str
    ) -> dict[str, Any]:
        self.calls.append(("get", integration_id, chat_id))
        return dict(self.session)

    def set_feishu_session(
        self,
        integration_id: str,
        chat_id: str,
        *,
        batch_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.calls.append(("set", integration_id, chat_id))
        if batch_id is not None:
            self.session["batch_id"] = batch_id
        if context is not None:
            self.session["context"] = dict(context)

    def __getattr__(self, name: str) -> Any:
        if name in {
            "get_bot_session",
            "set_bot_session",
            "get_bot_context",
            "set_bot_context",
        }:
            raise AssertionError(f"multi-tenant webhook reached legacy storage: {name}")
        raise AttributeError(name)


def test_integration_scoped_feishu_session_never_uses_legacy_tables() -> None:
    database = _IntegrationOnlyDatabase()
    store = FeishuSessionStore(database, integration_id="integration-a")  # type: ignore[arg-type]

    store.bind_batch("chat-a", "batch-a")
    store.update("chat-a", stage="reviewing")

    assert store.current_batch_id("chat-a") == "batch-a"
    assert store.get("chat-a")["stage"] == "reviewing"
    assert database.calls
    assert all(call[1:] == ("integration-a", "chat-a") for call in database.calls)
