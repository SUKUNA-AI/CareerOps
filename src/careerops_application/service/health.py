from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE_NAME = "careerops-application-owner"
SERVICE_PROTOCOL_VERSION = "p2-08"


class _ApplicationHealthServer(ThreadingHTTPServer):
    ready_event: threading.Event


class _HealthHandler(BaseHTTPRequestHandler):
    server_version = SERVICE_NAME

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._write_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": SERVICE_NAME,
                    "protocol_version": SERVICE_PROTOCOL_VERSION,
                },
            )
            return
        if self.path == "/readyz":
            server = self.server
            ready = isinstance(server, _ApplicationHealthServer) and server.ready_event.is_set()
            self._write_json(
                HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "status": "ready" if ready else "not_ready",
                    "service": SERVICE_NAME,
                },
            )
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"status": "not_found"})

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _write_json(self, status: HTTPStatus, body: dict[str, object]) -> None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class HealthServer:
    def __init__(self, host: str, port: int, *, ready_event: threading.Event) -> None:
        server = _ApplicationHealthServer((host, port), _HealthHandler)
        server.ready_event = ready_event
        self._server = server
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("health server is already started")
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="careerops-application-owner-health",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
