from __future__ import annotations

from types import TracebackType
from typing import Any, Self

import pytest

from careerops_processing.service import main as service_main
from careerops_processing.service.config import ProcessingRuntimeConfig


def _config() -> ProcessingRuntimeConfig:
    return ProcessingRuntimeConfig(
        postgres_dsn="postgresql://careerops@127.0.0.1/careerops",
        s3_endpoint_url="http://127.0.0.1:8333",
        s3_access_key="test-access",
        s3_secret_key="test-secret",
        s3_region="us-east-1",
        normalized_bucket="careerops-lake",
        artifacts_bucket="careerops-artifacts",
        artifacts_prefix="processing",
        worker_id="processing-test",
        worker_lease_seconds=60,
        worker_idle_sleep_seconds=0.01,
        health_host="127.0.0.1",
        health_port=18081,
    )


class _AsyncResource:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback


class _FakeConnection(_AsyncResource):
    created: list[_FakeConnection] = []

    def __init__(self, dsn: str, *, autocommit: bool) -> None:
        self.dsn = dsn
        self.autocommit = autocommit
        type(self).created.append(self)

    @classmethod
    async def connect(cls, dsn: str, *, autocommit: bool) -> _FakeConnection:
        return cls(dsn, autocommit=autocommit)


class _FakeS3Store(_AsyncResource):
    def __init__(self, settings: object) -> None:
        self.settings = settings


class _FakeArtifactStore(_AsyncResource):
    def __init__(self, settings: object) -> None:
        self.settings = settings


class _FakeHealthServer:
    instance: _FakeHealthServer | None = None

    def __init__(self, host: str, port: int, *, ready_event: Any) -> None:
        self.host = host
        self.port = port
        self.ready_event = ready_event
        self.started = False
        self.closed = False
        type(self).instance = self

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True
        assert not self.ready_event.is_set()


class _FakeWorker:
    instance: _FakeWorker | None = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.ready_seen = False
        type(self).instance = self

    async def run_forever(self, stop: Any) -> None:
        health = _FakeHealthServer.instance
        assert health is not None
        self.ready_seen = health.ready_event.is_set()
        stop.set()


@pytest.mark.asyncio
async def test_serve_composes_worker_and_exposes_readiness_only_after_wiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeConnection.created.clear()
    _FakeHealthServer.instance = None
    _FakeWorker.instance = None

    monkeypatch.setattr(service_main, "AsyncConnection", _FakeConnection)
    monkeypatch.setattr(service_main, "S3JsonStore", _FakeS3Store)
    monkeypatch.setattr(service_main, "ProcessingArtifactStore", _FakeArtifactStore)
    monkeypatch.setattr(service_main, "HealthServer", _FakeHealthServer)
    monkeypatch.setattr(service_main, "ProcessingWorker", _FakeWorker)
    monkeypatch.setattr(service_main, "_install_signal_handlers", lambda _stop: None)

    assert await service_main._serve_async(_config()) == 0

    assert len(_FakeConnection.created) == 2
    assert _FakeConnection.created[0] is not _FakeConnection.created[1]
    assert all(connection.autocommit for connection in _FakeConnection.created)

    health = _FakeHealthServer.instance
    assert health is not None
    assert health.started is True
    assert health.closed is True
    assert health.ready_event.is_set() is False

    worker = _FakeWorker.instance
    assert worker is not None
    assert worker.ready_seen is True
    assert worker.kwargs["worker_id"] == "processing-test"
    assert worker.kwargs["store"].__class__.__name__ == "PostgresProcessingJobStore"
    assert worker.kwargs["executor"].__class__.__name__ == "P204Executor"
