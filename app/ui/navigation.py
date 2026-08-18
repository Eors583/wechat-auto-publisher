from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlencode


def ui_root_url(query: Mapping[str, object] | None = None) -> str:
    """Return an internal UI URL that keeps the configured proxy subpath."""

    root_path = str(os.getenv("WECHAT_PUBLISHER_UI_ROOT_PATH") or "").strip("/")
    url = f"/{root_path}/" if root_path else "/"
    return f"{url}?{urlencode(query)}" if query else url


__all__ = ["ui_root_url"]
