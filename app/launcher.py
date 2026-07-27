from __future__ import annotations

import json
import logging
import multiprocessing
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from app.config import load_config, project_root


logger = logging.getLogger(__name__)


def _ensure_standard_streams() -> None:
    """Give windowed frozen processes valid streams for Uvicorn/NiceGUI.

    PyInstaller's windowed mode sets ``sys.stdout`` and ``sys.stderr`` to
    ``None``.  Uvicorn's default log formatter calls ``isatty()`` on those
    streams during startup, which otherwise aborts both the API and desktop
    servers before they bind their ports.
    """

    if sys.stdout is None or sys.stderr is None:
        log_dir = project_root() / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = (log_dir / "runtime-console.log").open(
            "a",
            encoding="utf-8",
            buffering=1,
        )
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream


def _configure_file_logging(name: str) -> None:
    log_dir = project_root() / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / name,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        encoding="utf-8",
        force=True,
    )


def _api_port() -> int:
    override = str(os.getenv("WECHAT_PUBLISHER_API_PORT") or "").strip()
    if override:
        return int(override)
    try:
        config = load_config()
        return int((config.get("api") or {}).get("port") or 18766)
    except Exception:  # noqa: BLE001
        return 18766


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.4):
            return True
    except OSError:
        return False


def _api_is_healthy(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{int(port)}/health", timeout=1.5) as response:
            payload: Any = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return False
        if str(payload.get("service") or "") != "wechat-auto-publisher":
            return False
        actual_root = str(payload.get("instance_root") or "")
        expected_root = str(project_root().resolve())
        if sys.platform == "win32":
            actual_root = actual_root.casefold()
            expected_root = expected_root.casefold()
        return actual_root == expected_root
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False


def _wait_for_api(port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + max(1.0, float(timeout))
    while time.monotonic() < deadline:
        if _api_is_healthy(port):
            return True
        time.sleep(0.25)
    return False


def _run_api_service() -> None:
    _ensure_standard_streams()
    _configure_file_logging("api.log")
    logger.info("Starting bundled API and Feishu service")
    try:
        from app.api.server import main

        main()
    except BaseException:  # noqa: BLE001
        logger.exception("Bundled API service stopped unexpectedly")
        raise


def _show_warning(message: str) -> None:
    logger.warning(message)
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            str(message),
            "公众号改写助手",
            0x00000030,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Unable to show launcher warning")


def _run_self_test() -> int:
    _configure_file_logging("self-test.log")
    from app.packaging_smoke import run_packaging_self_test

    report = run_packaging_self_test()
    return 0 if bool(report.get("ok")) else 1


def _run_ui_smoke_server(port: int) -> None:
    _configure_file_logging("ui-smoke.log")
    from nicegui import ui

    from app.ui.desktop import create_desktop_app

    ui.run(
        root=create_desktop_app,
        title="公众号改写助手安装验证",
        native=False,
        reload=False,
        show=False,
        port=int(port),
    )


def main() -> int:
    multiprocessing.freeze_support()
    _ensure_standard_streams()
    if "--self-test" in sys.argv:
        return _run_self_test()
    if "--ui-smoke-server" in sys.argv:
        index = sys.argv.index("--ui-smoke-server")
        if index + 1 >= len(sys.argv):
            raise ValueError("--ui-smoke-server requires a port")
        _run_ui_smoke_server(int(sys.argv[index + 1]))
        return 0
    if "--api-only" in sys.argv:
        _run_api_service()
        return 0

    _configure_file_logging("launcher.log")
    api_port = _api_port()
    api_process: multiprocessing.Process | None = None
    if not _api_is_healthy(api_port):
        if _port_is_open(api_port):
            _show_warning(
                f"本机端口 {api_port} 已被其他程序占用，飞书和 API 服务无法启动。"
                "桌面端仍会继续打开。"
            )
        else:
            api_process = multiprocessing.Process(
                target=_run_api_service,
                name="wechat-publisher-api",
                daemon=True,
            )
            api_process.start()
            if not _wait_for_api(api_port):
                _show_warning(
                    "API/飞书服务没有在预期时间内启动。"
                    "请打开 data/logs/api.log 查看原因。"
                )

    try:
        from app.ui.desktop import main as desktop_main

        desktop_main()
        return 0
    finally:
        if api_process is not None and api_process.is_alive():
            api_process.terminate()
            api_process.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
