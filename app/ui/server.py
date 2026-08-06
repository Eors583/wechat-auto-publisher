from __future__ import annotations

import os

from nicegui import app, ui
from starlette.responses import PlainTextResponse

from app.config import database_target, load_config
from app.ui.desktop import create_desktop_app


@app.get("/robots.txt", include_in_schema=False)
def robots_txt() -> PlainTextResponse:
    """Keep crawlers away from the authenticated internal application."""
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


def main() -> None:
    database_target(load_config())
    ui.run(
        root=create_desktop_app,
        host="0.0.0.0",
        port=int(os.getenv("WECHAT_PUBLISHER_UI_PORT") or "18765"),
        title="公众号智能运营助手",
        native=False,
        show=False,
        reload=False,
        reconnect_timeout=30.0,
        storage_secret=str(
            os.getenv("AUTH_STORAGE_SECRET")
            or "wechat-auto-publisher-local-storage-v1"
        ),
    )


if __name__ == "__main__":
    main()
