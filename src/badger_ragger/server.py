"""Small JSON HTTP adapter for Obsidian and SilverBullet on a trusted host."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from typing import Any

from .config import Config
from .index import IndexUnavailable
from .service import BadgerService, StaleIndexError, status_data


MAX_BODY_BYTES = 64 * 1024


class ApiError(ValueError):
    """An invalid HTTP request with a client-facing status code."""

    def __init__(self, message: str, code: int = 400) -> None:
        super().__init__(message)
        self.code = code


def create_server(
    config: Config,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str = "",
    service: BadgerService | None = None,
) -> ThreadingHTTPServer:
    """Create a local API; non-loopback binding requires a bearer token."""
    if host not in ("127.0.0.1", "::1", "localhost") and not token:
        raise ValueError("Set BADGER_API_TOKEN before binding outside localhost")
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    engine = service or BadgerService(config)

    class Handler(BaseHTTPRequestHandler):
        """Validate and dispatch one JSON request without serving note files."""

        def _send(self, code: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if not token:
                return True
            provided = self.headers.get("Authorization", "")
            return hmac.compare_digest(provided, f"Bearer {token}")

        def _read_body(self) -> dict[str, Any]:
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                raise ApiError("Content-Type must be application/json", 415)
            try:
                size = int(self.headers.get("Content-Length", ""))
            except ValueError as exc:
                raise ApiError("Content-Length is required") from exc
            if not 0 <= size <= MAX_BODY_BYTES:
                raise ApiError("Request body is too large", 413)
            try:
                payload = json.loads(self.rfile.read(size))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ApiError("Request body must be valid JSON") from exc
            if not isinstance(payload, dict):
                raise ApiError("Request body must be a JSON object")
            return payload

        def _dispatch(self) -> None:
            if not self._authorized():
                self._send(401, {"error": "Unauthorized"})
                return
            try:
                if self.command == "GET" and self.path == "/status":
                    self._send(200, {"status": engine.status()})
                elif self.command == "POST" and self.path == "/index":
                    body = self._read_body()
                    if type(body.get("rebuild", False)) is not bool:
                        raise ApiError("rebuild must be a boolean")
                    self._send(200, engine.index(rebuild=body.get("rebuild", False)))
                elif self.command == "POST" and self.path == "/search":
                    body = self._read_body()
                    self._send(200, engine.search(body.get("query"), body.get("top_k")))
                else:
                    self._send(404, {"error": "Unknown endpoint"})
            except StaleIndexError as exc:
                self._send(409, {
                    "error": str(exc),
                    "status": status_data(exc.status, indexed=config.index_path.is_file()),
                })
            except ApiError as exc:
                self._send(exc.code, {"error": str(exc)})
            except (ValueError, IndexUnavailable) as exc:
                self._send(400, {"error": str(exc)})
            except OSError as exc:
                self._send(500, {"error": f"Cannot access notes or index: {exc}"})
            except Exception as exc:
                # The HTTP boundary must still return JSON if model loading fails.
                self.log_error("Badger request failed: %s", exc)
                self._send(500, {"error": f"Badger could not complete the request: {exc}"})

        def do_GET(self) -> None:
            """Serve the read-only status endpoint."""
            self._dispatch()

        def do_POST(self) -> None:
            """Serve explicit indexing and inference actions."""
            self._dispatch()

        def log_message(self, format: str, *args: object) -> None:
            """Log endpoint and status without including query or note text."""
            super().log_message(format, *args)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
