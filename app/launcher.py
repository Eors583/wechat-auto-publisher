from __future__ import annotations

import json
import logging
import multiprocessing
import os
import socket
import sys
import time
import uuid
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from dotenv import load_dotenv

from app.config import database_target, load_config, project_root
from app.runtime_control import (
    ApiProcessControlError,
    OwnedApiProcessController,
    clear_api_process_controller,
    register_api_process_controller,
)

logger = logging.getLogger(__name__)


def _remote_ui_url(argv: list[str] | None = None) -> str:
    """Return the hosted UI URL for a thin desktop client.

    A remote desktop must never receive a PostgreSQL connection string or model
    secret. It only renders the UI hosted by the merchant server. HTTPS is
    recommended; plain HTTP remains supported for explicit IP-and-port
    deployments on private or temporarily restricted environments.
    """

    arguments = list(sys.argv[1:] if argv is None else argv)
    value = ""
    if "--remote-url" in arguments:
        index = arguments.index("--remote-url")
        if index + 1 >= len(arguments):
            raise ValueError("--remote-url requires an HTTP(S) URL")
        value = str(arguments[index + 1] or "").strip()
    if not value:
        load_dotenv(project_root() / ".env")
        value = str(os.getenv("WECHAT_PUBLISHER_REMOTE_URL") or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("远程工作台必须使用完整的 HTTP 或 HTTPS 地址")
    return value.rstrip("/")


def _run_remote_desktop(url: str) -> int:
    """Open the hosted NiceGUI application without starting local services."""

    import webview

    webview.create_window(
        "公众号智能运营助手",
        url,
        width=1180,
        height=860,
        min_size=(980, 720),
    )
    webview.start()
    return 0


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
    # httpx logs complete request URLs at INFO level. WeChat token and API
    # requests carry AppSecret/access_token in the query string, so these
    # transport logs must never be persisted. Lark websocket URLs likewise
    # contain short-lived connection credentials.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("Lark").setLevel(logging.WARNING)


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


def _api_health_payload(port: int) -> dict[str, Any] | None:
    try:
        with urlopen(f"http://127.0.0.1:{int(port)}/health", timeout=1.5) as response:
            payload: Any = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return None
        if str(payload.get("service") or "") != "wechat-auto-publisher":
            return None
        actual_root = str(payload.get("instance_root") or "")
        expected_root = str(project_root().resolve())
        if sys.platform == "win32":
            actual_root = actual_root.casefold()
            expected_root = expected_root.casefold()
        return payload if actual_root == expected_root else None
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return None


def _api_is_healthy(
    port: int,
    expected_session_id: str = "",
) -> bool:
    payload = _api_health_payload(port)
    if payload is None:
        return False
    expected_session_id = str(expected_session_id or "").strip()
    if expected_session_id and str(
        payload.get("launcher_session_id") or ""
    ).strip() != expected_session_id:
        return False
    return True


def _wait_for_api(
    port: int,
    timeout: float = 20.0,
    expected_session_id: str = "",
) -> bool:
    deadline = time.monotonic() + max(1.0, float(timeout))
    while time.monotonic() < deadline:
        if _api_is_healthy(port, expected_session_id):
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


def _mark_feishu_restarting() -> None:
    """Persist restart telemetry without exposing or reloading any secret."""

    try:
        from app.db import Database
        from app.feishu.runtime import update_runtime

        config = load_config()
        update_runtime(
            Database(database_target(config)),
            status="restarting",
            last_error="",
        )
    except Exception:  # runtime telemetry must never prevent a restart
        logger.exception("Unable to mark Feishu runtime as restarting")


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
        storage_secret=f"package-smoke-{uuid.uuid4().hex}",
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
    try:
        remote_url = _remote_ui_url()
    except ValueError as exc:
        _show_warning(str(exc))
        return 2
    if remote_url:
        logger.info("Starting hosted desktop client: %s", remote_url)
        return _run_remote_desktop(remote_url)

    try:
        database_target(load_config())
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        _show_warning(f"本地模式启动失败：{exc}")
        return 2

    api_port = _api_port()
    launcher_session_id = uuid.uuid4().hex
    os.environ["WECHAT_PUBLISHER_LAUNCH_SESSION_ID"] = launcher_session_id
    api_controller = OwnedApiProcessController(
        port=api_port,
        target=_run_api_service,
        session_id=launcher_session_id,
        health_check=_api_is_healthy,
        port_check=_port_is_open,
        wait_for_health=_wait_for_api,
        before_restart=_mark_feishu_restarting,
    )
    register_api_process_controller(api_controller)
    try:
        api_controller.ensure_started()
    except ApiProcessControlError as exc:
        _show_warning(str(exc))

    try:
        from app.ui.desktop import main as desktop_main

        desktop_main()
        return 0
    finally:
        api_controller.shutdown()
        clear_api_process_controller(api_controller)


if __name__ == "__main__":
    raise SystemExit(main())
