from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.local_model_cors_bridge import create_handler


ORIGIN = "https://api.bluebloodlab.cn"


class _Upstream(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _serve(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def test_bridge_adds_private_network_cors_and_proxies_requests() -> None:
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    _serve(upstream)
    upstream_url = f"http://127.0.0.1:{upstream.server_port}"
    bridge = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(upstream=upstream_url, allowed_origin=ORIGIN),
    )
    _serve(bridge)
    base = f"http://127.0.0.1:{bridge.server_port}"
    try:
        preflight = Request(
            f"{base}/v1/chat/completions",
            method="OPTIONS",
            headers={"Origin": ORIGIN},
        )
        with urlopen(preflight) as response:  # noqa: S310
            assert response.status == 204
            assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
            assert (
                response.headers["Access-Control-Allow-Private-Network"]
                == "true"
            )
            assert "Authorization" in response.headers[
                "Access-Control-Allow-Headers"
            ]

        request = Request(
            f"{base}/v1/chat/completions",
            data=b"{}",
            method="POST",
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        )
        with urlopen(request) as response:  # noqa: S310
            assert json.load(response) == {"ok": True}
            assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
    finally:
        bridge.shutdown()
        upstream.shutdown()


def test_bridge_rejects_other_web_origins() -> None:
    bridge = ThreadingHTTPServer(("127.0.0.1", 0), create_handler())
    _serve(bridge)
    try:
        request = Request(
            f"http://127.0.0.1:{bridge.server_port}/v1/models",
            headers={"Origin": "https://evil.example"},
        )
        try:
            urlopen(request)  # noqa: S310
        except HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("untrusted browser origin must be rejected")
    finally:
        bridge.shutdown()
