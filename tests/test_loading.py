from __future__ import annotations

from typing import Any

from app.ui import state as state_module
from app.ui.loading import RequestLoading


class _FakeButton:
    def __init__(self) -> None:
        self.props_calls: list[dict[str, str]] = []
        self.disabled = False

    def props(self, **kwargs: str) -> None:
        self.props_calls.append(kwargs)

    def disable(self) -> None:
        self.disabled = True

    def enable(self) -> None:
        self.disabled = False


class _FakeOverlay:
    def __init__(self) -> None:
        self.shown: list[str] = []
        self.hidden = 0

    def show(self, message: str) -> None:
        self.shown.append(message)

    def hide(self) -> None:
        self.hidden += 1


def test_button_loading_also_controls_request_overlay(monkeypatch: Any) -> None:
    button = _FakeButton()
    overlay = _FakeOverlay()

    def fake_loading(target: Any, message: str) -> _FakeOverlay:
        target._request_loading_overlay = overlay
        return overlay

    monkeypatch.setattr(state_module, "get_request_loading", fake_loading)

    state_module.set_button_loading(button, True, "正在写入两个公众号草稿箱")
    assert button.disabled is True
    assert button.props_calls[-1] == {"add": "loading"}
    assert overlay.shown == ["正在写入两个公众号草稿箱"]

    state_module.set_button_loading(button, False)
    assert button.disabled is False
    assert button.props_calls[-1] == {"remove": "loading"}
    assert overlay.hidden == 1


def test_button_loading_ignores_deleted_elements(monkeypatch: Any) -> None:
    button = _FakeButton()
    button.is_deleted = True

    def fail_loading(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("deleted button must not create an overlay")

    monkeypatch.setattr(state_module, "get_request_loading", fail_loading)

    state_module.set_button_loading(button, True)
    state_module.set_button_loading(button, False)

    assert button.props_calls == []
    assert button.disabled is False


def test_button_loading_can_offer_a_non_blocking_background_action(
    monkeypatch: Any,
) -> None:
    button = _FakeButton()
    calls: list[dict[str, Any]] = []

    class _BackgroundOverlay(_FakeOverlay):
        def show(self, message: str, **kwargs: Any) -> None:
            calls.append({"message": message, **kwargs})

    overlay = _BackgroundOverlay()

    def fake_loading(target: Any, _message: str) -> _BackgroundOverlay:
        target._request_loading_overlay = overlay
        return overlay

    monkeypatch.setattr(state_module, "get_request_loading", fake_loading)
    callback = lambda: None

    state_module.set_button_loading(
        button,
        True,
        "AI 正在改写",
        on_background=callback,
        background_label="转入后台改写",
    )

    assert calls == [
        {
            "message": "AI 正在改写",
            "on_background": callback,
            "background_label": "转入后台改写",
        }
    ]
    assert button.disabled is True


def test_background_action_closes_overlay_without_cancelling_request() -> None:
    events: list[str] = []

    class _Dialog:
        def close(self) -> None:
            events.append("closed")

    overlay = RequestLoading.__new__(RequestLoading)
    overlay.dialog = _Dialog()
    overlay._background_handler = lambda: events.append("continued")

    overlay._enter_background()

    assert events == ["closed", "continued"]
