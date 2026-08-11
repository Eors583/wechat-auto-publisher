from __future__ import annotations

import json
from typing import Any

from nicegui import core, ui


def install_interaction_feedback() -> None:
    """Install one non-blocking request indicator for the current page.

    The client-side listener paints before NiceGUI sends the event over its
    websocket, so a slow network or server query never looks like a dead click.
    """

    ui.add_body_html(
        """
        <div id="ops-interaction-feedback"
             class="ops-interaction-feedback"
             role="status" aria-live="polite" aria-atomic="true" hidden>
          <span class="ops-interaction-marker" aria-hidden="true">•••</span>
          <span class="ops-interaction-feedback-copy">
            <strong data-role="title">正在加载</strong>
            <span data-role="detail">请求已提交，请稍候…</span>
          </span>
        </div>
        <script>
          (() => {
            let slowTimer = null;
            let safetyTimer = null;
            window.opsInteractionFeedback = {
              show(message) {
                const root = document.getElementById('ops-interaction-feedback');
                if (!root) return;
                root.querySelector('[data-role="title"]').textContent = message || '正在加载';
                root.querySelector('[data-role="detail"]').textContent = '请求已提交，请稍候…';
                root.hidden = false;
                root.setAttribute('aria-busy', 'true');
                clearTimeout(slowTimer);
                clearTimeout(safetyTimer);
                slowTimer = setTimeout(() => {
                  const detail = root.querySelector('[data-role="detail"]');
                  if (!root.hidden && detail) {
                    detail.textContent = '网络或服务响应较慢，仍在处理中…';
                  }
                }, 1200);
                safetyTimer = setTimeout(() => this.hide(), 30000);
              },
              hide() {
                const root = document.getElementById('ops-interaction-feedback');
                clearTimeout(slowTimer);
                clearTimeout(safetyTimer);
                if (!root) return;
                root.hidden = true;
                root.removeAttribute('aria-busy');
              },
            };
          })();
        </script>
        """
    )


def attach_interaction_feedback(
    element: Any,
    message: str,
    *,
    event: str = "click",
) -> Any:
    """Paint request feedback immediately, before emitting the server event."""

    encoded = json.dumps(str(message or "正在加载"), ensure_ascii=False)
    element.on(
        event,
        js_handler=f"() => window.opsInteractionFeedback?.show({encoded})",
    )
    return element


def hide_interaction_feedback(client: Any | None = None) -> None:
    """Hide the page-level indicator after the requested view has rendered."""

    target = client or ui.context.client
    if (
        target is None
        or bool(getattr(target, "is_deleted", False))
        or core.loop is None
    ):
        return
    target.run_javascript("window.opsInteractionFeedback?.hide()")


__all__ = [
    "attach_interaction_feedback",
    "hide_interaction_feedback",
    "install_interaction_feedback",
]
