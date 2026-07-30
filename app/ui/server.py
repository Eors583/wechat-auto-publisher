from __future__ import annotations

import os

from nicegui import ui

from app.ui.desktop import create_desktop_app


def main() -> None:
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
