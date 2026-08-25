from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlencode


def ui_root_url(query: Mapping[str, object] | None = None) -> str:
    """Return an internal UI URL that keeps the configured proxy subpath."""

    root_path = str(os.getenv("WECHAT_PUBLISHER_UI_ROOT_PATH") or "").strip("/")
    url = f"/{root_path}/" if root_path else "/"
    return f"{url}?{urlencode(query)}" if query else url


def ui_navigation_target(url: str) -> str:
    """Remove the proxy prefix that NiceGUI adds during client navigation."""

    root_path = str(os.getenv("WECHAT_PUBLISHER_UI_ROOT_PATH") or "").strip("/")
    prefix = f"/{root_path}" if root_path else ""
    if prefix and (
        url == prefix
        or url.startswith(f"{prefix}/")
        or url.startswith(f"{prefix}?")
    ):
        return url[len(prefix) :] or "/"
    return url


__all__ = ["ui_navigation_target", "ui_root_url"]
