from __future__ import annotations

from typing import Any

from nicegui import ui


DEFAULT_REQUEST_MESSAGE = "正在处理请求，请稍候…"


class RequestLoading:
    """Reusable blocking loading overlay for user-triggered API requests."""

    def __init__(self, message: str = DEFAULT_REQUEST_MESSAGE) -> None:
        with ui.dialog().props(
            "persistent no-esc-dismiss no-backdrop-dismiss "
            "transition-show=fade transition-hide=fade"
        ).classes("request-loading-dialog") as self.dialog:
            with ui.card().classes("request-loading-card items-center"):
                ui.spinner("dots", size="54px", color="teal-9")
                self.message_label = ui.label(message).classes(
                    "request-loading-message text-center"
                )
                ui.label("请求完成后会自动关闭，请勿重复操作").classes(
                    "muted text-center"
                )

    def show(self, message: str = DEFAULT_REQUEST_MESSAGE) -> None:
        self.message_label.text = message or DEFAULT_REQUEST_MESSAGE
        self.dialog.open()

    def update(self, message: str) -> None:
        self.message_label.text = message or DEFAULT_REQUEST_MESSAGE

    def hide(self) -> None:
        self.dialog.close()


def get_request_loading(button: Any, message: str) -> RequestLoading:
    """Return the loading overlay owned by a request button.

    Keeping it on the button makes the component local to the current browser
    client and avoids sharing NiceGUI elements between sessions.
    """
    overlay = getattr(button, "_request_loading_overlay", None)
    if overlay is None:
        overlay = RequestLoading(message)
        setattr(button, "_request_loading_overlay", overlay)
    return overlay
