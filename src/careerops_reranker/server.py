"""Внутренний HTTP server отдельного Jina reranker runtime"""

from __future__ import annotations

import json
import logging
import signal
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .config import RerankerRuntimeConfig
from .runtime import JinaRerankerRuntime, TokenBudgetExceeded

_LOG = logging.getLogger(__name__)
_MAX_REQUEST_BYTES = 4 * 1024 * 1024


class _ExpectedRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    model_code_revision: str = Field(min_length=1)
    tokenizer_revision: str = Field(min_length=1)
    runtime_backend: str = Field(min_length=1)
    dtype_or_quantization: str = Field(min_length=1)
    torch_version: str = Field(min_length=1)
    transformers_version: str = Field(min_length=1)


class _RerankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1)
    documents: tuple[str, ...] = Field(min_length=1)
    top_n: int = Field(gt=0)
    token_budget: int = Field(gt=0)
    expected_runtime: _ExpectedRuntime

    @model_validator(mode="after")
    def validate_pool(self) -> _RerankRequest:
        if self.top_n > len(self.documents):
            raise ValueError("top_n превышает число documents")
        if any(not item.strip() for item in self.documents):
            raise ValueError("documents не должны содержать пустые строки")
        return self


class _RerankerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], runtime: JinaRerankerRuntime) -> None:
        self.runtime = runtime
        super().__init__(server_address, _RerankerHandler)


class _RerankerHandler(BaseHTTPRequestHandler):
    server: _RerankerServer

    def log_message(self, format: str, *args: object) -> None:
        _LOG.info("reranker_http " + format, *args)

    def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _runtime_identity(self) -> dict[str, str]:
        return self.server.runtime.identity.as_dict()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == "/readyz":
            self._write_json(
                HTTPStatus.OK,
                {"status": "ready", "runtime": self._runtime_identity()},
            )
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/rerank":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        raw_length = self.headers.get("Content-Length", "")
        try:
            content_length = int(raw_length)
        except ValueError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
            return
        if content_length <= 0 or content_length > _MAX_REQUEST_BYTES:
            self._write_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "request_too_large"},
            )
            return

        body = self.rfile.read(content_length)
        try:
            request = _RerankRequest.model_validate_json(body)
        except ValidationError as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request", "details": exc.errors(include_url=False)},
            )
            return

        expected = request.expected_runtime.model_dump(mode="json")
        if expected != self._runtime_identity():
            self._write_json(
                HTTPStatus.CONFLICT,
                {"error": "runtime_identity_mismatch", "runtime": self._runtime_identity()},
            )
            return

        try:
            results, total_tokens = self.server.runtime.rerank(
                query=request.query,
                documents=request.documents,
                top_n=request.top_n,
                token_budget=request.token_budget,
            )
        except TokenBudgetExceeded as exc:
            self._write_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "token_budget_exceeded", "message": str(exc)},
            )
            return
        except ValueError as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request", "message": str(exc)},
            )
            return
        except Exception:
            _LOG.exception("ошибка Jina inference")
            self._write_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "inference_unavailable"},
            )
            return

        self._write_json(
            HTTPStatus.OK,
            {
                "runtime": self._runtime_identity(),
                "usage": {"total_tokens": total_tokens},
                "results": list(results),
            },
        )


def _install_signal_handlers(stop: threading.Event) -> None:
    def request_stop(_signum: int, _frame: Any) -> None:
        stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, request_stop)


def serve(config: RerankerRuntimeConfig) -> int:
    """Загружает model до readiness и обслуживает внутренний HTTP endpoint"""

    runtime = JinaRerankerRuntime.load(config)
    stop = threading.Event()
    _install_signal_handlers(stop)
    server = _RerankerServer((config.host, config.port), runtime)
    server.timeout = 0.5
    try:
        while not stop.is_set():
            server.handle_request()
    finally:
        server.server_close()
    return 0