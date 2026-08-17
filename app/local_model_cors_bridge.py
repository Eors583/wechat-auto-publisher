from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_ORIGIN = "https://api.bluebloodlab.cn"
DEFAULT_UPSTREAM = "http://127.0.0.1:11797"
MAX_BODY_BYTES = 16 * 1024 * 1024


def create_handler(
    *, upstream: str = DEFAULT_UPSTREAM, allowed_origin: str = DEFAULT_ORIGIN
) -> type[BaseHTTPRequestHandler]:
    upstream = upstream.rstrip("/")

    class BridgeHandler(BaseHTTPRequestHandler):
        def _cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, Accept",
            )
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")

        def _origin_allowed(self) -> bool:
            return self.headers.get("Origin") in {None, allowed_origin}

        def _send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802
            if not self._origin_allowed():
                self._send_json(403, {"error": "origin not allowed"})
                return
            self.send_response(204)
            self._cors_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            self._proxy()

        def do_POST(self) -> None:  # noqa: N802
            self._proxy()

        def _proxy(self) -> None:
            if not self._origin_allowed():
                self._send_json(403, {"error": "origin not allowed"})
                return
            if self.path == "/health":
                self._send_json(200, {"ok": True, "upstream": upstream})
                return
            if not self.path.startswith("/v1/"):
                self._send_json(404, {"error": "only /v1/* is supported"})
                return

            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._send_json(413, {"error": "request body too large"})
                return
            body = self.rfile.read(length) if length else None
            headers = {
                name: value
                for name in ("Authorization", "Content-Type", "Accept")
                if (value := self.headers.get(name))
            }
            try:
                request = Request(
                    f"{upstream}{self.path}",
                    data=body,
                    headers=headers,
                    method=self.command,
                )
                with urlopen(request, timeout=620) as response:  # noqa: S310
                    response_body = response.read()
                    self.send_response(response.status)
                    self._cors_headers()
                    self.send_header(
                        "Content-Type",
                        response.headers.get("Content-Type", "application/json"),
                    )
                    self.send_header("Content-Length", str(len(response_body)))
                    self.end_headers()
                    self.wfile.write(response_body)
            except HTTPError as exc:
                response_body = exc.read()
                self.send_response(exc.code)
                self._cors_headers()
                self.send_header(
                    "Content-Type",
                    exc.headers.get("Content-Type", "application/json"),
                )
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)
            except (URLError, TimeoutError) as exc:
                self._send_json(502, {"error": f"Cockpit Tools unavailable: {exc}"})

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return BridgeHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Cockpit Tools browser bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11798)
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    args = parser.parse_args()
    server = ThreadingHTTPServer(
        (args.host, args.port),
        create_handler(upstream=args.upstream, allowed_origin=args.origin),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
