from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui


def client_timer(
    interval: float,
    callback: Callable[..., Any],
    *,
    active: bool = True,
    once: bool = False,
    immediate: bool = True,
) -> Any:
    """Create a timer whose lifetime is bound to the current UI client.

    NiceGUI timers inherit the slot in which they are created. If that slot is
    refreshed or a client is deleted while a timer is between iterations, the
    timer may otherwise try to re-enter a deleted slot. Mounting the timer on
    the stable client content slot and cancelling it before client elements are
    removed prevents callbacks from touching deleted UI elements.
    """

    client = ui.context.client
    with client.content:
        timer = ui.timer(
            interval,
            callback,
            active=active,
            once=once,
            immediate=immediate,
        )

    # Tests may replace ui.timer with a lightweight stub.
    if timer is None or not callable(getattr(timer, "cancel", None)):
        return timer

    def cancel_timer() -> None:
        if not bool(getattr(timer, "_is_canceled", False)):
            timer.cancel(with_current_invocation=True)

    client.on_delete(cancel_timer)
    return timer


__all__ = ["client_timer"]
