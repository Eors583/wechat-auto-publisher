from __future__ import annotations

import argparse
import hmac
import html
import json
import secrets
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from app.local_credentials import LocalCredentialStore


DEFAULT_ORIGIN = "https://api.bluebloodlab.cn"
DEFAULT_HOST = "127.0.0.1:11798"
DEFAULT_UPSTREAM = "http://127.0.0.1:11797"
MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_SETUP_BYTES = 16 * 1024
MODEL_ROUTES = {
    ("GET", "/v1/models"),
    ("POST", "/v1/chat/completions"),
}


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _read_limited(response: Any) -> bytes:
    body = response.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("response body too large")
    return body


def create_handler(
    *,
    upstream: str = DEFAULT_UPSTREAM,
    allowed_origin: str = DEFAULT_ORIGIN,
    allowed_host: str = DEFAULT_HOST,
    credential_store: Any | None = None,
    agent_controller: Any | None = None,
    request_timeout: float = 620.0,
) -> type[BaseHTTPRequestHandler]:
    """Create the loopback-only HTTP handler used by the Windows bridge."""

    upstream = upstream.rstrip("/")
    store = credential_store or LocalCredentialStore()
    csrf_token = secrets.token_urlsafe(32)
    # Cockpit is always loopback. Never inherit system proxy settings and never
    # follow a redirect that could carry the locally stored Bearer key away.
    local_opener = build_opener(ProxyHandler({}), _NoRedirectHandler())

    class BridgeHandler(BaseHTTPRequestHandler):
        server_version = "BlueBloodLabCockpitBridge/1"
        sys_version = ""

        def _expected_host(self) -> str:
            if allowed_host:
                return allowed_host
            address, port = self.server.server_address[:2]
            return f"{address}:{port}"

        def _host_allowed(self) -> bool:
            return hmac.compare_digest(
                str(self.headers.get("Host") or "").casefold(),
                self._expected_host().casefold(),
            )

        def _origin_allowed(self, *, required: bool) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return not required
            return hmac.compare_digest(origin, allowed_origin)

        def _cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
            # Retained for Chrome versions that still send the legacy PNA
            # preflight. Modern Chrome/Edge additionally require site-level LNA.
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")

        def _security_headers(self, *, referrer_policy: str = "no-referrer") -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", referrer_policy)
            self.send_header("Cache-Control", "no-store")

        def _send_json(
            self,
            status: int,
            payload: dict[str, object],
            *,
            cors: bool,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            if cors:
                self._cors_headers()
            self._security_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_setup(self, status: int, message: str = "") -> None:
            configured = bool(store.configured())
            safe_message = html.escape(str(message or ""))
            state = "已在本机安全保存" if configured else "尚未配置"
            agent_section = ""
            if agent_controller is not None:
                try:
                    agent = dict(agent_controller.public_status() or {})
                except Exception:
                    agent = {"paired": False, "connection_status": "error"}
                paired = bool(agent.get("paired"))
                pairing_id = html.escape(str(agent.get("pairing_id") or ""))
                user_code = html.escape(str(agent.get("user_code") or ""))
                verification_uri = html.escape(
                    str(agent.get("verification_uri_complete") or ""),
                    quote=True,
                )
                connection_status = html.escape(
                    str(agent.get("connection_status") or "未连接")
                )
                autostart_enabled = bool(agent.get("autostart_enabled"))
                pairing_detail = (
                    f'<p class="state">配对码：<strong>{user_code}</strong></p>'
                    f'<p><a href="{verification_uri}" target="_blank" rel="noopener">'
                    "打开生产工作台并批准此设备</a></p>"
                    f'<p class="small">配对请求：{pairing_id}</p>'
                    if user_code and verification_uri
                    else ""
                )
                pairing_action = (
                    '<p class="small">如需更换账号或重新配对，请先在生产工作台撤销此设备；'
                    "本机助手确认撤销后会重新显示配对入口。</p>"
                    if paired
                    else f"""
<form method="post" action="/setup">
<input type="hidden" name="csrf_token" value="{csrf_token}">
<input type="hidden" name="action" value="start_pairing">
<button type="submit">生成一次性配对码</button></form>
"""
                )
                agent_section = f"""
<hr><h2>生产 Companion</h2>
<p class="state">配对状态：{'已配对' if paired else '未配对'}；生产连接：{connection_status}</p>
{pairing_detail}
{pairing_action}
<form method="post" action="/setup">
<input type="hidden" name="csrf_token" value="{csrf_token}">
<input type="hidden" name="action" value="set_autostart">
<input type="hidden" name="enabled" value="{'0' if autostart_enabled else '1'}">
<button type="submit">{'关闭登录自启动' if autostart_enabled else '启用登录自启动'}</button></form>
<form method="post" action="/setup">
<input type="hidden" name="csrf_token" value="{csrf_token}">
<input type="hidden" name="action" value="stop_agent">
<button type="submit">当前任务结束后退出本机助手</button></form>
"""
            body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cockpit Tools 本机助手</title>
<style>
body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#f5f7fb;color:#172033;margin:0}}
main{{box-sizing:border-box;max-width:680px;margin:48px auto;padding:28px;background:#fff;border-radius:16px;box-shadow:0 8px 30px #1d31521a}}
h1{{font-size:24px;margin:0 0 10px}}p{{line-height:1.65;overflow-wrap:anywhere}}label{{display:block;font-weight:650;margin:22px 0 8px}}
input{{box-sizing:border-box;width:100%;padding:12px;border:1px solid #aab5c5;border-radius:8px;font:inherit}}
button{{margin-top:16px;padding:11px 18px;border:0;border-radius:8px;background:#087f6d;color:#fff;font:inherit;font-weight:700;cursor:pointer}}
.state{{padding:12px;border-radius:8px;background:#edf7f5}}.message{{padding:12px;border-radius:8px;background:#fff4d7;color:#744d00}}
.small{{color:#5d6878;font-size:14px}}hr{{border:0;border-top:1px solid #dde3ec;margin:28px 0}}a{{color:#087f6d;font-weight:700}}</style></head>
<body><main><h1>Cockpit Tools 本机助手</h1>
<p class="state">密钥状态：{state}</p>
{f'<p class="message">{safe_message}</p>' if safe_message else ''}
<p>密钥只会经 Windows CurrentUser DPAPI 加密后保存在这台电脑，不会发送到生产服务器。</p>
<form method="post" action="/setup" autocomplete="off">
<input type="hidden" name="csrf_token" value="{csrf_token}">
<input type="hidden" name="action" value="save_key">
<label for="api_key">新的 Cockpit API Key</label>
<input id="api_key" name="api_key" type="password" required maxlength="4096" autocomplete="new-password">
<button type="submit">验证并保存到本机</button></form>
<p class="small">保存前会直接调用 127.0.0.1:11797/v1/models 验证密钥。请勿把密钥粘贴到生产网页或发送给任何人。</p>
{agent_section}
</main></body></html>""".encode("utf-8")
            self.send_response(status)
            self._security_headers(referrer_policy="same-origin")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                "frame-ancestors 'none'; base-uri 'none'",
            )
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _load_key(self) -> str:
            try:
                return str(store.load_api_key() or "").strip()
            except Exception:
                return ""

        def _probe_cockpit(self, api_key: str) -> tuple[str, int]:
            request = Request(
                f"{upstream}/v1/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
                method="GET",
            )
            try:
                with local_opener.open(
                    request,
                    timeout=min(request_timeout, 8.0),
                ) as response:  # noqa: S310
                    _read_limited(response)
                return "ready", 200
            except HTTPError as exc:
                if exc.code in {401, 403}:
                    return "unauthorized", exc.code
                if exc.code == 404:
                    return "endpoint_not_found", exc.code
                if exc.code == 429:
                    return "rate_limited", exc.code
                return "upstream_error", exc.code
            except (OSError, URLError, TimeoutError, socket.timeout, ValueError):
                return "unavailable", 0

        def do_OPTIONS(self) -> None:  # noqa: N802
            cors = self.headers.get("Origin") == allowed_origin
            if not self._host_allowed():
                self._send_json(403, {"error": "host not allowed"}, cors=cors)
                return
            if not self._origin_allowed(required=True):
                self._send_json(403, {"error": "origin not allowed"}, cors=False)
                return
            path = self.path.split("?", 1)[0]
            requested_method = str(
                self.headers.get("Access-Control-Request-Method") or "GET"
            ).upper()
            allowed = (
                requested_method == "GET"
                if path == "/health"
                else (requested_method, path) in MODEL_ROUTES
            )
            if not allowed:
                self._send_json(405, {"error": "method not allowed"}, cors=True)
                return
            requested_headers = {
                item.strip().casefold()
                for item in str(
                    self.headers.get("Access-Control-Request-Headers") or ""
                ).split(",")
                if item.strip()
            }
            if not requested_headers.issubset({"content-type", "accept"}):
                self._send_json(403, {"error": "request headers not allowed"}, cors=True)
                return
            self.send_response(204)
            self._cors_headers()
            self._security_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/setup":
                if not self._host_allowed():
                    self._send_json(403, {"error": "host not allowed"}, cors=False)
                    return
                self._send_setup(200)
                return
            if path == "/health":
                self._health()
                return
            self._proxy_model()

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/setup":
                self._save_setup()
                return
            self._proxy_model()

        def _health(self) -> None:
            cors = self.headers.get("Origin") == allowed_origin
            if not self._host_allowed():
                self._send_json(403, {"error": "host not allowed"}, cors=cors)
                return
            if not self._origin_allowed(required=False):
                self._send_json(403, {"error": "origin not allowed"}, cors=False)
                return
            key = self._load_key()
            cockpit_status = "key_not_configured"
            if key:
                cockpit_status, _ = self._probe_cockpit(key)
            self._send_json(
                200,
                {
                    "bridge_ready": True,
                    "key_configured": bool(key),
                    "cockpit_status": cockpit_status,
                },
                cors=cors,
            )

        def _save_setup(self) -> None:
            if not self._host_allowed():
                self._send_json(403, {"error": "host not allowed"}, cors=False)
                return
            if self.headers.get("Origin") != f"http://{self._expected_host()}":
                self._send_json(403, {"error": "setup origin not allowed"}, cors=False)
                return
            if not str(self.headers.get("Content-Type") or "").casefold().startswith(
                "application/x-www-form-urlencoded"
            ):
                self._send_json(415, {"error": "unsupported content type"}, cors=False)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length <= 0 or length > MAX_SETUP_BYTES:
                self._send_setup(413, "提交内容无效或过大。")
                return
            try:
                form = parse_qs(
                    self.rfile.read(length).decode("utf-8", errors="strict"),
                    keep_blank_values=True,
                )
            except UnicodeDecodeError:
                self._send_setup(400, "提交内容编码无效。")
                return
            submitted_csrf = str((form.get("csrf_token") or [""])[0])
            if not hmac.compare_digest(submitted_csrf, csrf_token):
                self._send_setup(403, "页面已过期，请刷新后重试。")
                return
            action = str((form.get("action") or ["save_key"])[0])
            if action == "start_pairing" and agent_controller is not None:
                try:
                    already_paired = bool(
                        dict(agent_controller.public_status() or {}).get("paired")
                    )
                except Exception:
                    already_paired = False
                if already_paired:
                    self._send_setup(
                        409,
                        "本机助手已配对；请先在生产工作台撤销旧设备。",
                    )
                    return
                try:
                    agent_controller.start_pairing()
                except Exception:
                    self._send_setup(502, "无法连接生产服务，请检查网络后重试。")
                    return
                self._send_setup(200, "一次性配对码已生成，请在生产工作台批准。")
                return
            if action == "set_autostart" and agent_controller is not None:
                try:
                    enabled = str((form.get("enabled") or ["0"])[0]) == "1"
                    agent_controller.set_autostart(enabled)
                except Exception:
                    self._send_setup(500, "无法更新 Windows 登录自启动设置。")
                    return
                self._send_setup(200, "Windows 登录自启动设置已更新。")
                return
            if action == "stop_agent" and agent_controller is not None:
                self._send_setup(200, "本机助手正在退出；本页稍后将无法访问。")
                agent_controller.request_stop()
                return
            api_key = str((form.get("api_key") or [""])[0]).strip()
            if not api_key or len(api_key) > 4096:
                self._send_setup(400, "请输入有效的 Cockpit API Key。")
                return
            cockpit_status, status_code = self._probe_cockpit(api_key)
            if cockpit_status != "ready":
                messages = {
                    "unauthorized": "Cockpit API Key 无效，请生成新 Key 后重试。",
                    "endpoint_not_found": "Cockpit Tools 的 /v1/models 接口不可用。",
                    "rate_limited": "Cockpit Tools 当前限流，请稍后重试。",
                    "unavailable": "无法连接 127.0.0.1:11797，请先启动 Cockpit Tools API 服务。",
                    "upstream_error": f"Cockpit Tools 返回 HTTP {status_code}。",
                }
                self._send_setup(400, messages.get(cockpit_status, "验证失败。"))
                return
            try:
                store.save_api_key(api_key)
            except Exception:
                self._send_setup(500, "密钥无法写入 Windows 当前用户凭据存储。")
                return
            self._send_setup(200, "验证成功，密钥已经安全保存到本机。")

        def _proxy_model(self) -> None:
            cors = self.headers.get("Origin") == allowed_origin
            path = self.path.split("?", 1)[0]
            if not self._host_allowed():
                self._send_json(403, {"error": "host not allowed"}, cors=cors)
                return
            if not self._origin_allowed(required=True):
                self._send_json(403, {"error": "origin not allowed"}, cors=False)
                return
            if (self.command, path) not in MODEL_ROUTES:
                self._send_json(404, {"error": "route not supported"}, cors=True)
                return
            if self.headers.get("Authorization"):
                self._send_json(
                    400,
                    {"error": "browser authorization is not accepted"},
                    cors=True,
                )
                return
            body: bytes | None = None
            if self.command == "POST":
                content_type = str(self.headers.get("Content-Type") or "")
                if content_type.split(";", 1)[0].strip().casefold() != "application/json":
                    self._send_json(415, {"error": "JSON required"}, cors=True)
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = -1
                if length < 0 or length > MAX_BODY_BYTES:
                    self._send_json(413, {"error": "request body too large"}, cors=True)
                    return
                body = self.rfile.read(length)
                try:
                    json.loads(body or b"{}")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._send_json(400, {"error": "invalid JSON"}, cors=True)
                    return
            api_key = self._load_key()
            if not api_key:
                self._send_json(
                    428,
                    {
                        "error": {
                            "code": "cockpit_key_not_configured",
                            "message": "请先打开本机助手设置页配置 Cockpit API Key",
                        }
                    },
                    cors=True,
                )
                return
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            }
            if self.command == "POST":
                headers["Content-Type"] = "application/json"
            try:
                request = Request(
                    f"{upstream}{path}",
                    data=body,
                    headers=headers,
                    method=self.command,
                )
                with local_opener.open(
                    request,
                    timeout=request_timeout,
                ) as response:  # noqa: S310
                    response_body = _read_limited(response)
                    if api_key.encode("utf-8") in response_body:
                        self._send_json(
                            502,
                            {"error": "Cockpit response contained a protected credential"},
                            cors=True,
                        )
                        return
                    self.send_response(response.status)
                    self._cors_headers()
                    self._security_headers()
                    self.send_header(
                        "Content-Type",
                        response.headers.get("Content-Type", "application/json"),
                    )
                    self.send_header("Content-Length", str(len(response_body)))
                    self.end_headers()
                    self.wfile.write(response_body)
            except HTTPError as exc:
                messages = {
                    401: "Cockpit API Key 无效",
                    403: "Cockpit 拒绝访问，请检查本机 API Key 权限",
                    404: "Cockpit 模型接口或模型名称不存在",
                    429: "Cockpit 当前请求过多，请稍后重试",
                }
                status = int(exc.code)
                self._send_json(
                    status,
                    {
                        "error": {
                            "code": f"cockpit_http_{status}",
                            "message": messages.get(
                                status,
                                "Cockpit Tools 返回上游错误",
                            ),
                        }
                    },
                    cors=True,
                )
            except (TimeoutError, socket.timeout):
                self._send_json(
                    504,
                    {
                        "error": {
                            "code": "cockpit_timeout",
                            "message": "Cockpit 模型调用超时",
                        }
                    },
                    cors=True,
                )
            except URLError as exc:
                if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                    self._send_json(
                        504,
                        {
                            "error": {
                                "code": "cockpit_timeout",
                                "message": "Cockpit 模型调用超时",
                            }
                        },
                        cors=True,
                    )
                    return
                self._send_json(
                    502,
                    {
                        "error": {
                            "code": "cockpit_unavailable",
                            "message": "Cockpit Tools 11797 未启动或不可达",
                        }
                    },
                    cors=True,
                )
            except OSError:
                self._send_json(
                    502,
                    {
                        "error": {
                            "code": "cockpit_unavailable",
                            "message": "Cockpit Tools 11797 未启动或不可达",
                        }
                    },
                    cors=True,
                )
            except ValueError:
                self._send_json(502, {"error": "upstream response too large"}, cors=True)

        def log_message(self, _format: str, *_args: object) -> None:
            # Request paths, bodies and Authorization values are intentionally
            # never persisted by the local bridge.
            return

    return BridgeHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Cockpit Tools browser bridge")
    parser.add_argument("--port", type=int, default=11798)
    args = parser.parse_args()
    host = "127.0.0.1"
    allowed_host = f"{host}:{args.port}"
    server = ThreadingHTTPServer(
        (host, args.port),
        create_handler(allowed_host=allowed_host),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
