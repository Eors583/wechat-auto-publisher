from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from app.local_model_cors_bridge import MAX_BODY_BYTES, create_handler


ORIGIN = "https://api.bluebloodlab.cn"


class _MemoryStore:
    def __init__(self, key: str = "") -> None:
        self.key = key

    def configured(self) -> bool:
        return bool(self.key)

    def load_api_key(self) -> str:
        return self.key

    def save_api_key(self, api_key: str) -> None:
        self.key = api_key


def _upstream_handler(state: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    class _Upstream(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._respond()

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            state["body"] = self.rfile.read(length)
            self._respond()

        def _respond(self) -> None:
            state["authorization"] = self.headers.get("Authorization")
            state["path"] = self.path
            if state.get("delay"):
                time.sleep(float(state["delay"]))
            if state["authorization"] != f"Bearer {state['expected_key']}":
                status = 401
                payload: Any = {"error": "invalid key"}
            else:
                status = int(state.get("status") or 200)
                if state.get("leak_error") and status >= 400:
                    payload = {"error": state["authorization"]}
                elif state.get("leak_success"):
                    payload = {"choices": [{"message": {"content": state["authorization"]}}]}
                elif self.path == "/v1/models":
                    payload = {"data": [{"id": "gpt-5.5"}]}
                else:
                    payload = {
                        "choices": [
                            {"message": {"content": "OK"}, "finish_reason": "stop"}
                        ]
                    }
            body = state.get("raw_body") or json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return _Upstream


def _serve(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


@contextmanager
def _running_bridge(
    *,
    key: str = "local-secret",
    status: int = 200,
    request_timeout: float = 1,
) -> Iterator[tuple[str, _MemoryStore, dict[str, Any]]]:
    state: dict[str, Any] = {
        "expected_key": "local-secret",
        "status": status,
    }
    upstream = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        _upstream_handler(state),
    )
    _serve(upstream)
    store = _MemoryStore(key)
    bridge = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(
            upstream=f"http://127.0.0.1:{upstream.server_port}",
            allowed_origin=ORIGIN,
            allowed_host="",
            credential_store=store,
            request_timeout=request_timeout,
        ),
    )
    _serve(bridge)
    try:
        yield f"http://127.0.0.1:{bridge.server_port}", store, state
    finally:
        bridge.shutdown()
        upstream.shutdown()


def _open_json(request: Request) -> tuple[int, dict[str, Any], Any]:
    try:
        response = urlopen(request)  # noqa: S310
    except HTTPError as exc:
        return exc.code, json.load(exc), exc.headers
    with response:
        return response.status, json.load(response), response.headers


def test_bridge_adds_private_network_cors_and_injects_local_key() -> None:
    with _running_bridge() as (base, _store, state):
        preflight = Request(
            f"{base}/v1/chat/completions",
            method="OPTIONS",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, Accept",
            },
        )
        with urlopen(preflight) as response:  # noqa: S310
            assert response.status == 204
            assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
            assert response.headers["Access-Control-Allow-Private-Network"] == "true"
            assert response.headers["Access-Control-Allow-Headers"] == (
                "Content-Type, Accept"
            )
            assert "Authorization" not in response.headers[
                "Access-Control-Allow-Headers"
            ]

        request = Request(
            f"{base}/v1/chat/completions",
            data=json.dumps({"model": "gpt-5.5", "messages": []}).encode(),
            method="POST",
            headers={
                "Origin": ORIGIN,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        status, payload, headers = _open_json(request)
        assert status == 200
        assert payload["choices"][0]["message"]["content"] == "OK"
        assert headers["Access-Control-Allow-Origin"] == ORIGIN
        assert state["authorization"] == "Bearer local-secret"
        assert b"local-secret" not in state["body"]


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, 403),
        ({"Origin": "https://evil.example"}, 403),
        ({"Origin": ORIGIN, "Authorization": "Bearer browser-key"}, 400),
    ],
)
def test_model_routes_reject_missing_origin_other_origins_and_browser_keys(
    headers: dict[str, str],
    expected: int,
) -> None:
    with _running_bridge() as (base, _store, _state):
        request = Request(f"{base}/v1/models", headers=headers)
        status, _payload, _response_headers = _open_json(request)
        assert status == expected


def test_bridge_rejects_wrong_host_and_unknown_routes() -> None:
    with _running_bridge() as (base, _store, _state):
        request = Request(
            f"{base}/v1/models",
            headers={"Origin": ORIGIN, "Host": "evil.example"},
        )
        assert _open_json(request)[0] == 403

        unknown = Request(
            f"{base}/v1/arbitrary",
            headers={"Origin": ORIGIN},
        )
        assert _open_json(unknown)[0] == 404


def test_health_is_sanitized_and_distinguishes_key_and_cockpit_state() -> None:
    with _running_bridge(key="") as (base, store, _state):
        request = Request(f"{base}/health", headers={"Origin": ORIGIN})
        status, payload, _headers = _open_json(request)
        assert status == 200
        assert payload == {
            "bridge_ready": True,
            "key_configured": False,
            "cockpit_status": "key_not_configured",
        }
        assert "upstream" not in payload

        store.key = "local-secret"
        payload = _open_json(request)[1]
        assert payload["key_configured"] is True
        assert payload["cockpit_status"] == "ready"
        assert "local-secret" not in json.dumps(payload)


def test_setup_page_uses_csrf_security_headers_and_never_echoes_key() -> None:
    with _running_bridge(key="") as (base, store, _state):
        with urlopen(f"{base}/setup") as response:  # noqa: S310
            page = response.read().decode("utf-8")
            assert response.headers["X-Frame-Options"] == "DENY"
            assert "frame-ancestors 'none'" in response.headers[
                "Content-Security-Policy"
            ]
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Referrer-Policy"] == "same-origin"
        token = re.search(r'name="csrf_token" value="([^"]+)"', page)
        assert token is not None

        api_key = "local-secret"
        body = urlencode(
            {"csrf_token": token.group(1), "api_key": api_key}
        ).encode()
        request = Request(
            f"{base}/setup",
            data=body,
            method="POST",
            headers={
                "Origin": base,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urlopen(request) as response:  # noqa: S310
            saved_page = response.read().decode("utf-8")
        assert store.key == api_key
        assert api_key not in saved_page
        assert "验证成功" in saved_page

        for origin in (None, "null", "https://evil.example"):
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            if origin is not None:
                headers["Origin"] = origin
            rejected = Request(
                f"{base}/setup",
                data=body,
                method="POST",
                headers=headers,
            )
            with pytest.raises(HTTPError) as error:
                urlopen(rejected)  # noqa: S310
            assert error.value.code == 403

        invalid = Request(
            f"{base}/setup",
            data=urlencode(
                {"csrf_token": "wrong", "api_key": "do-not-store"}
            ).encode(),
            method="POST",
            headers={
                "Origin": base,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with pytest.raises(HTTPError) as error:
            urlopen(invalid)  # noqa: S310
        assert error.value.code == 403
        assert store.key == api_key


@pytest.mark.parametrize("upstream_status", [401, 404, 429, 500])
def test_bridge_preserves_safe_upstream_http_status(upstream_status: int) -> None:
    with _running_bridge(status=upstream_status) as (base, _store, _state):
        request = Request(
            f"{base}/v1/chat/completions",
            data=b"{}",
            method="POST",
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        )
        assert _open_json(request)[0] == upstream_status


def test_bridge_never_returns_a_key_echoed_by_upstream() -> None:
    with _running_bridge(status=500) as (base, _store, state):
        state["leak_error"] = True
        request = Request(
            f"{base}/v1/chat/completions",
            data=b"{}",
            method="POST",
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        )
        status, payload, _headers = _open_json(request)
        assert status == 500
        assert "local-secret" not in json.dumps(payload)

    with _running_bridge() as (base, _store, state):
        state["leak_success"] = True
        request = Request(
            f"{base}/v1/chat/completions",
            data=b"{}",
            method="POST",
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        )
        status, payload, _headers = _open_json(request)
        assert status == 502
        assert "local-secret" not in json.dumps(payload)


def test_bridge_ignores_system_http_proxy_for_cockpit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy_state = {"hits": 0}

    class _Proxy(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            proxy_state["hits"] += 1
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_GET

        def log_message(self, _format: str, *_args: object) -> None:
            return

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), _Proxy)
    _serve(proxy)
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.server_port}")
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{proxy.server_port}")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    try:
        with _running_bridge() as (base, _store, state):
            port = int(base.rsplit(":", 1)[1])
            connection = HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request(
                "GET",
                "/v1/models",
                headers={"Origin": ORIGIN, "Accept": "application/json"},
            )
            response = connection.getresponse()
            assert response.status == 200
            response.read()
            connection.close()
            assert state["authorization"] == "Bearer local-secret"
            assert proxy_state["hits"] == 0
    finally:
        proxy.shutdown()


def test_bridge_reports_timeout_and_oversized_upstream_response() -> None:
    with _running_bridge(request_timeout=0.02) as (base, _store, state):
        state["delay"] = 0.1
        request = Request(
            f"{base}/v1/chat/completions",
            data=b"{}",
            method="POST",
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        )
        status, payload, _headers = _open_json(request)
        assert status == 504
        assert payload["error"]["code"] == "cockpit_timeout"

    with _running_bridge() as (base, _store, state):
        state["raw_body"] = b"x" * (MAX_BODY_BYTES + 1)
        request = Request(
            f"{base}/v1/chat/completions",
            data=b"{}",
            method="POST",
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        )
        status, payload, _headers = _open_json(request)
        assert status == 502
        assert payload["error"] == "upstream response too large"


def test_bridge_reports_missing_key_and_unavailable_cockpit() -> None:
    with _running_bridge(key="") as (base, _store, _state):
        request = Request(
            f"{base}/v1/chat/completions",
            data=b"{}",
            method="POST",
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        )
        status, payload, _headers = _open_json(request)
        assert status == 428
        assert payload["error"]["code"] == "cockpit_key_not_configured"

    store = _MemoryStore("local-secret")
    bridge = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(
            upstream="http://127.0.0.1:1",
            allowed_host="",
            credential_store=store,
            request_timeout=0.05,
        ),
    )
    _serve(bridge)
    try:
        request = Request(
            f"http://127.0.0.1:{bridge.server_port}/v1/chat/completions",
            data=b"{}",
            method="POST",
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        )
        status, payload, _headers = _open_json(request)
        assert status in {502, 504}
        assert payload["error"]["code"] in {
            "cockpit_unavailable",
            "cockpit_timeout",
        }
    finally:
        bridge.shutdown()


def test_bridge_rejects_oversized_requests_without_reading_the_body() -> None:
    with _running_bridge() as (base, _store, _state):
        port = int(base.rsplit(":", 1)[1])
        connection = HTTPConnection("127.0.0.1", port, timeout=2)
        connection.putrequest("POST", "/v1/chat/completions")
        connection.putheader("Origin", ORIGIN)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(MAX_BODY_BYTES + 1))
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 413
        connection.close()
