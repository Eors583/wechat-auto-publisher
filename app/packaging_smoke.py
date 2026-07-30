from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

from app.config import database_target, load_config, project_root


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

    def load_runtime_config() -> str:
        config_holder.update(load_config())
        return str(config_holder["_root"])

    check("runtime_config", load_runtime_config)

    def database_schema() -> str:
        from app.db import Database

        database = Database(database_target(config_holder))
        with database.connect() as connection:
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            account_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(official_accounts)"
                ).fetchall()
            }
        required_tables = {
            "job_attempts",
            "draft_deliveries",
            "wechat_connection_health",
        }
        missing = sorted(required_tables - tables)
        if missing:
            raise RuntimeError(f"P0 数据表缺失：{missing}")
        if "review_priority" not in account_columns:
            raise RuntimeError("公众号审核优先级字段缺失")
        return (
            f"accounts={len(database.list_official_accounts())} "
            f"p0_tables={len(required_tables)}"
        )

    check("database_schema", database_schema)

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
        from app.api.server import create_api_app

        application = create_api_app(config_holder, start_feishu=False)
        # NiceGUI/FastAPI can add internal router sentinels without a ``path``
        # attribute. They are not HTTP endpoints and must not fail the frozen
        # installation self-test.
        paths = _api_route_paths(application)
        required = {
            "/health",
            "/api/v1/accounts",
            "/api/v1/batches",
            "/api/v1/review-inbox",
            "/api/v1/onboarding/status",
            "/api/v1/batches/{batch_id}/jobs/{job_id}/retry",
            "/api/v1/batches/{batch_id}/jobs/{job_id}/attempts",
            "/api/v1/wechat/connection-health",
            "/api/v1/topics/hot",
        }
        missing = sorted(required - paths)
        if missing:
            raise RuntimeError(f"API 路由缺失：{missing}")
        return f"routes={len(paths)}"

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
