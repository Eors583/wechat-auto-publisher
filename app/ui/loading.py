from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

DEFAULT_REQUEST_MESSAGE = "正在处理请求，请稍候…"


class RequestLoading:
    """Reusable non-blocking status surface for user-triggered API requests."""

    def __init__(self, message: str = DEFAULT_REQUEST_MESSAGE) -> None:
        self._background_handler: Callable[[], None] | None = None
        with ui.dialog().props(
            "seamless position=top transition-show=fade transition-hide=fade"
        ).classes("request-loading-dialog") as self.dialog:
            with ui.card().classes("request-loading-card"):
                ui.spinner("dots", size="28px", color="primary")
                self.message_label = ui.label(message).classes(
                    "request-loading-message"
                )
                self.helper_label = ui.label(
                    "请求已经提交，可继续查看页面；完成后会自动关闭"
                ).classes(
                    "muted"
                )
                self.background_button = ui.button(
                    "转入后台处理",
                    on_click=self._enter_background,
                ).props(
                    "outline color=teal-9 no-caps icon=move_to_inbox"
                ).classes("q-mt-sm")
                self.background_button.set_visibility(False)

    def show(
        self,
        message: str = DEFAULT_REQUEST_MESSAGE,
        *,
        on_background: Callable[[], None] | None = None,
        background_label: str = "转入后台处理",
    ) -> None:
        self.message_label.text = message or DEFAULT_REQUEST_MESSAGE
        self._background_handler = on_background
        self.background_button.text = background_label
        self.background_button.set_visibility(on_background is not None)
        self.helper_label.text = (
            "转入后台后任务会继续执行，可继续使用其他功能"
            if on_background is not None
            else "请求已经提交，可继续查看页面；完成后会自动关闭"
        )
        self.dialog.open()

    def update(self, message: str) -> None:
        self.message_label.text = message or DEFAULT_REQUEST_MESSAGE

    def hide(self) -> None:
        self.dialog.close()

    def _enter_background(self) -> None:
        """Dismiss the blocker without cancelling the active request."""

        handler = self._background_handler
        self.dialog.close()
        if handler is not None:
            handler()


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
