"""Headless browser entry point for local development and verification."""

import os

from nicegui import ui

from app.ui.desktop import create_desktop_app
from app.ui import styles


def main() -> None:
    port = int(str(os.getenv("WECHAT_PUBLISHER_UI_PORT") or "18765"))
    print(
        f"UI source: {styles.__file__} | "
        f"layout={'float-v2' if 'float: left' in styles.APP_CSS else 'legacy-grid'} | "
        f"port={port}",
        flush=True,
    )
    ui.run(
        root=create_desktop_app,
        title="Wechat Publisher",
        reload=False,
        reconnect_timeout=30.0,
        port=port,
        show=False,
    )


if __name__ == "__main__":
    main()
