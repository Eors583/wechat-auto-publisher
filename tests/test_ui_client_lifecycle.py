from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any, Callable

from nicegui import ui

from app.ui import desktop
from app.ui import lifecycle
from app.ui.panels import tasks


class _FakeContent:
    def __init__(self) -> None:
        self.active = False

    def __enter__(self) -> "_FakeContent":
        self.active = True
        return self

    def __exit__(self, *_args: object) -> None:
        self.active = False


class _FakeClient:
    def __init__(self) -> None:
        self.content = _FakeContent()
        self.delete_handlers: list[Callable[[], None]] = []

    def on_delete(self, handler: Callable[[], None]) -> None:
        self.delete_handlers.append(handler)

    def delete(self) -> None:
        for handler in list(self.delete_handlers):
            handler()


class _FakeTimer:
    def __init__(self) -> None:
        self._is_canceled = False
        self.cancel_calls: list[bool] = []

    def cancel(self, *, with_current_invocation: bool = False) -> None:
        self.cancel_calls.append(with_current_invocation)
        self._is_canceled = True


def test_client_timer_is_cancelled_when_its_client_is_deleted(
    monkeypatch: Any,
) -> None:
    client = _FakeClient()
    timer = _FakeTimer()
    created_in_client_content: list[bool] = []

    def fake_timer(
        _interval: float,
        _callback: Callable[..., Any],
        **_kwargs: Any,
    ) -> _FakeTimer:
        created_in_client_content.append(client.content.active)
        return timer

    monkeypatch.setattr(
        lifecycle.ui,
        "context",
        SimpleNamespace(client=client),
    )
    monkeypatch.setattr(lifecycle.ui, "timer", fake_timer)

    result = lifecycle.client_timer(1.0, lambda: None)

    assert result is timer
    assert created_in_client_content == [True]
    assert len(client.delete_handlers) == 1
    assert timer.cancel_calls == []

    client.delete()

    assert timer.cancel_calls == [True]


def test_retry_loading_skips_deleted_client_after_background_work(
    monkeypatch: Any,
) -> None:
    client = SimpleNamespace(is_deleted=False)
    button = object()
    loading: list[bool] = []
    service_calls: list[tuple[str, int]] = []

    class _Service:
        def retry_job(
            self,
            batch_id: str,
            job_id: int,
            **_kwargs: object,
        ) -> dict[str, bool]:
            service_calls.append((batch_id, job_id))
            return {"accepted": True}

    async def finish_after_delete(callback: Callable[[], Any]) -> Any:
        result = callback()
        client.is_deleted = True
        return result

    monkeypatch.setattr(tasks.run, "io_bound", finish_after_delete)
    monkeypatch.setattr(
        tasks,
        "set_button_loading",
        lambda active_button, value: (
            active_button is button and loading.append(value)
        ),
    )

    result = asyncio.run(
        tasks._retry_job_with_loading(  # noqa: SLF001
            _Service(),  # type: ignore[arg-type]
            "batch-1",
            12,
            button,
            owner_client=client,
        )
    )

    assert result == {"accepted": True}
    assert service_calls == [("batch-1", 12)]
    assert loading == [True]


def test_retry_callbacks_guard_deleted_client_ui_updates() -> None:
    helper_source = inspect.getsource(tasks._retry_job_with_loading)  # noqa: SLF001
    dialog_source = inspect.getsource(tasks.open_retry_job_dialog)
    card_source = inspect.getsource(tasks._render_inbox_article_card)  # noqa: SLF001

    assert "_set_retry_loading_safely(" in helper_source
    assert "owner_client = ui.context.client" in dialog_source
    assert "owner_client=owner_client" in dialog_source
    assert "if not _ui_client_alive(owner_client)" in dialog_source
    assert "except RuntimeError" in dialog_source
    assert "owner_client=owner_client" in card_source
    assert "if not _ui_client_alive(owner_client)" in card_source


def test_create_desktop_app_uses_one_private_state_per_page_and_shares_it_with_panels(
    monkeypatch: Any,
) -> None:
    created_states: list[object] = []
    panel_calls: list[tuple[str, object]] = []
    account_refresh_callbacks: list[Callable[[], None]] = []
    plan_refresh_callbacks: list[Callable[[], None]] = []

    class _PageState:
        def __init__(self) -> None:
            created_states.append(self)

    def record(name: str, value: object) -> None:
        panel_calls.append((name, value))

    def fake_wizard(*_args: object, state: object, **_kwargs: object) -> None:
        record("wizard", state)

    def fake_topic_center(state: object, *_args: object, **_kwargs: object) -> None:
        record("topics", state)

    def fake_tasks_panel(state: object, *_args: object, **_kwargs: object) -> None:
        record("tasks", state)

    def fake_accounts_panel(
        state: object,
        *_args: object,
        **_kwargs: object,
    ) -> Callable[[], None]:
        record("accounts", state)

        def refresh() -> None:
            return None

        account_refresh_callbacks.append(refresh)
        return refresh

    def fake_models_panel(state: object, *_args: object, **_kwargs: object) -> None:
        record("models", state)

    def fake_plans_panel(
        state: object,
        *,
        on_plans_change: Callable[[], None],
        **_kwargs: object,
    ) -> None:
        record("plans", state)
        plan_refresh_callbacks.append(on_plans_change)

    def fake_feishu_panel(state: object, *_args: object, **_kwargs: object) -> None:
        record("feishu", state)

    monkeypatch.setattr(desktop, "AppState", _PageState)
    monkeypatch.setattr(desktop, "state", object())
    monkeypatch.setattr(desktop, "_build_wizard", fake_wizard)
    monkeypatch.setattr(desktop, "build_topic_center", fake_topic_center)
    monkeypatch.setattr(desktop, "build_tasks_panel", fake_tasks_panel)
    monkeypatch.setattr(desktop, "_build_accounts_panel", fake_accounts_panel)
    monkeypatch.setattr(desktop, "build_model_management_panel", fake_models_panel)
    monkeypatch.setattr(desktop, "build_creation_plans_panel", fake_plans_panel)
    monkeypatch.setattr(desktop, "build_feishu_panel", fake_feishu_panel)
    monkeypatch.setattr(desktop, "_build_help_panel", lambda: None)
    monkeypatch.setattr(desktop.ui, "add_head_html", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(desktop.ui, "add_css", lambda *_args, **_kwargs: None)

    expected_panels = {
        "wizard",
        "topics",
        "tasks",
        "accounts",
        "models",
        "plans",
        "feishu",
    }

    try:
        desktop.create_desktop_app()
        first_page_calls = list(panel_calls)
        panel_calls.clear()
        ui.context.client.remove_all_elements()

        desktop.create_desktop_app()
        second_page_calls = list(panel_calls)
    finally:
        ui.context.client.remove_all_elements()

    assert len(created_states) == 2
    assert created_states[0] is not created_states[1]
    assert {name for name, _state in first_page_calls} == expected_panels
    assert {name for name, _state in second_page_calls} == expected_panels
    assert all(state is created_states[0] for _name, state in first_page_calls)
    assert all(state is created_states[1] for _name, state in second_page_calls)
    assert plan_refresh_callbacks == account_refresh_callbacks
