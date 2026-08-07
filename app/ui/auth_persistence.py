from __future__ import annotations

import os
from typing import Any

from app.services.auth import SESSION_DAYS


AUTH_SESSION_MAX_AGE_SECONDS = SESSION_DAYS * 24 * 60 * 60


def auth_session_middleware_kwargs() -> dict[str, Any]:
    """Return browser-cookie settings aligned with persisted auth sessions.

    NiceGUI keeps the opaque login token in ``app.storage.user`` and uses a
    signed browser cookie to identify that storage.  The cookie must live at
    least as long as the database session, otherwise a still-valid server-side
    session becomes unreachable after the browser is closed or restarted.
    """

    secure_cookie = str(
        os.getenv("AUTH_SESSION_COOKIE_SECURE") or "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    return {
        "max_age": AUTH_SESSION_MAX_AGE_SECONDS,
        "same_site": "lax",
        "https_only": secure_cookie,
    }


__all__ = [
    "AUTH_SESSION_MAX_AGE_SECONDS",
    "auth_session_middleware_kwargs",
]
