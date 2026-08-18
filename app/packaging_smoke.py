from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

from app.config import database_target, load_config, project_root
from app.db_backend import is_postgres_url


def _open_local_url(url: str, *, timeout: float = 2.0) -> Any:
    """Open a loopback smoke-test URL without inheriting system proxies."""

    return build_opener(ProxyHandler({})).open(url, timeout=timeout)


def _api_route_paths(application: Any) -> set[str]:
    """Return public route paths while ignoring framework-internal entries."""

    return {
        str(path)
        for route in application.routes
        if (path := getattr(route, "path", None))
    }


def _packaging_remote_url(argv: list[str] | None = None) -> str:
    """Return the hosted UI configured for a thin production client."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    value = ""
    if "--remote-url" in arguments:
        index = arguments.index("--remote-url")
        if index + 1 >= len(arguments):
            raise ValueError("--remote-url requires an HTTP(S) URL")
        value = str(arguments[index + 1] or "").strip()
    if not value:
        value = str(os.getenv("WECHAT_PUBLISHER_REMOTE_URL") or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("远程工作台必须使用完整的 HTTP 或 HTTPS 地址")
    return value.rstrip("/")


def _runtime_storage_contract(config: dict[str, Any], remote_url: str) -> str:
    """Validate the selected runtime without opening or creating a database."""

    if remote_url:
        import webview

        if not callable(getattr(webview, "create_window", None)):
            raise RuntimeError("远程桌面 WebView 组件不可用")
        return f"remote-client={remote_url}"

    target = database_target(config)
    if not is_postgres_url(target):
        raise RuntimeError("本地后端运行模式只支持 PostgreSQL")
    import psycopg

    if not callable(getattr(psycopg, "connect", None)):
        raise RuntimeError("PostgreSQL 驱动不可用")
    parsed = urlparse(target)
    if not parsed.hostname or not parsed.path.strip("/"):
        raise RuntimeError("PostgreSQL 连接地址缺少主机或数据库名")
    return f"postgresql={parsed.hostname}/{parsed.path.strip('/')}"


def run_packaging_self_test() -> dict[str, Any]:
    """Run offline checks against a frozen or source installation.

    The self-test intentionally avoids external model and WeChat requests.  It
    verifies that the installer contains the modules and resources required to
    reach those services once the operator supplies credentials.
    """

    root = project_root()
    checks: list[dict[str, Any]] = []

    def check(name: str, callback: Callable[[], Any]) -> None:
        try:
            detail = callback()
            checks.append({"name": name, "ok": True, "detail": str(detail or "ok")})
        except Exception as exc:  # noqa: BLE001
            checks.append(
                {
                    "name": name,
                    "ok": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )

    config_holder: dict[str, Any] = {}
    remote_holder: dict[str, str] = {}

    def load_runtime_config() -> str:
        config_holder.update(load_config())
        remote_holder["url"] = _packaging_remote_url()
        mode = "remote-client" if remote_holder["url"] else "postgresql-backend"
        return f"root={config_holder['_root']} mode={mode}"

    check("runtime_config", load_runtime_config)

    check(
        "runtime_storage",
        lambda: _runtime_storage_contract(
            config_holder,
            remote_holder.get("url", ""),
        ),
    )

    def render_template() -> str:
        from app.render import TemplateRenderer

        renderer = TemplateRenderer(config_holder)
        template_path = renderer.template_dir / renderer.template_name
        if not template_path.is_file():
            raise RuntimeError(f"模板文件不存在：{template_path}")
        html = renderer.render(
            body="# 安装包测试\n\n这是离线渲染测试正文。"
        )
        if "安装包测试" not in html or "离线渲染测试正文" not in html:
            raise RuntimeError("渲染结果缺少正文")
        return f"html_chars={len(html)} template={template_path}"

    check("article_template", render_template)

    def api_routes() -> str:
        from app.api.server import create_api_app, main

        if not callable(create_api_app) or not callable(main):
            raise RuntimeError("API 服务入口不完整")
        # Application construction runs database migrations. An offline
        # installer check must never mutate a database, and a remote desktop
        # client does not own a database in the first place.
        return "factory+entrypoint"

    check("api_and_feishu_runtime", api_routes)

    def feishu_tools() -> str:
        from app.feishu.tool_catalog import TOOL_SPECS
        from app.feishu.tool_executor import FeishuToolExecutor

        missing = [
            name
            for name in TOOL_SPECS
            if not callable(getattr(FeishuToolExecutor, f"_tool_{name}", None))
        ]
        if missing:
            raise RuntimeError(f"飞书工具处理器缺失：{missing}")
        return f"tools={len(TOOL_SPECS)}"

    check("feishu_tool_contracts", feishu_tools)

    def external_sdks() -> str:
        import certifi
        import google.genai
        import lark_oapi
        import lark_oapi.ws
        from lark_oapi.api.im.v1 import CreateMessageRequest

        from app.feishu.gateway import FeishuGateway

        ca_file = Path(certifi.where())
        if not ca_file.is_file():
            raise RuntimeError(f"CA 证书不存在：{ca_file}")
        if not CreateMessageRequest or not FeishuGateway:
            raise RuntimeError("飞书 SDK 请求类不可用")
        return (
            f"lark={lark_oapi.__name__} google={google.genai.__name__} "
            f"ca={ca_file.name}"
        )

    check("external_sdk_resources", external_sdks)

    def local_agent_runtime() -> str:
        from app.local_agent import local_agent_self_test

        with tempfile.TemporaryDirectory(prefix="blueblood-agent-smoke-") as directory:
            result = local_agent_self_test(directory)
        if not bool(result.get("ok")):
            raise RuntimeError("本机 Companion DPAPI 状态往返失败")
        return f"{result['loopback_bind']} -> {result['remote_origin']}"

    check("local_agent_runtime", local_agent_runtime)

    def image_providers() -> str:
        from app.ai.image_providers import IMAGE_PROVIDER_PRESETS

        required = {"image_alibaba", "image_minimax"}
        missing = sorted(required - set(IMAGE_PROVIDER_PRESETS))
        if missing:
            raise RuntimeError(f"生图厂商模板缺失：{missing}")
        return f"providers={len(IMAGE_PROVIDER_PRESETS)}"

    check("image_provider_presets", image_providers)

    def wechat_relay_runtime() -> str:
        from app.services.wechat_relay_settings import (
            validate_wechat_relay_settings,
        )
        from app.ui.panels.wechat_relay import build_wechat_relay_panel
        from app.wechat.factory import build_wechat_auth, build_wechat_client

        disabled = validate_wechat_relay_settings({"enabled": False})
        if disabled["enabled"]:
            raise RuntimeError("离线自检不应启用微信云中转")
        if not all(
            callable(item)
            for item in (
                build_wechat_auth,
                build_wechat_client,
                build_wechat_relay_panel,
            )
        ):
            raise RuntimeError("微信云中转运行组件不完整")
        return "settings+factory+ui"

    check("wechat_relay_runtime", wechat_relay_runtime)

    def frozen_ui_home() -> str:
        if not bool(getattr(sys, "frozen", False)):
            return "source-mode skipped"
        if remote_holder.get("url"):
            return "remote-client skipped"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = int(listener.getsockname()[1])
        process = subprocess.Popen(  # noqa: S603
            [sys.executable, "--ui-smoke-server", str(port)],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 30.0
        last_error = ""
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"桌面验证进程提前退出：{process.returncode}"
                    )
                try:
                    with _open_local_url(
                        f"http://127.0.0.1:{port}/",
                        timeout=2.0,
                    ) as response:
                        html = response.read().decode("utf-8", errors="replace")
                    if response.status == 200 and "公众号改写助手" in html:
                        return f"status=200 html_chars={len(html)}"
                    last_error = f"HTTP {response.status}"
                except HTTPError as exc:
                    last_error = f"HTTP {exc.code}"
                except (OSError, URLError) as exc:
                    last_error = str(exc)
                time.sleep(0.25)
            raise RuntimeError(f"桌面首页验证超时：{last_error or '未监听'}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    check("desktop_home_page", frozen_ui_home)

    def writable_runtime_data() -> str:
        target = root / "data" / ".installer-write-test"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
        if target.read_text(encoding="utf-8") != "ok":
            raise RuntimeError("运行目录写入校验失败")
        target.unlink()
        return str(target.parent)

    check("writable_runtime_data", writable_runtime_data)

    report = {
        "ok": all(bool(item["ok"]) for item in checks),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(root),
        "frozen": bool(getattr(__import__("sys"), "frozen", False)),
        "checks": checks,
    }
    log_dir = root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "package-self-test.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report
