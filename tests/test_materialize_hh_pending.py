from __future__ import annotations

from io import StringIO
from typing import Any
from uuid import UUID

import pytest
from support.postgres import TransactionRecorder

import scripts.materialize_hh_pending as materializer
from careerops_etl.hh_s3_to_postgres import LoadedBatchResult

RUN_DONE = UUID("11111111-1111-4111-8111-111111111111")
RUN_PENDING = UUID("22222222-2222-4222-8222-222222222222")
RUN_APPLY = UUID("33333333-3333-4333-8333-333333333333")


class FakeStore:
    async def iter_keys(self, prefix: str = ""):
        assert prefix == "batches"

        for run_id in (RUN_DONE, RUN_PENDING, RUN_APPLY):
            base = f"batches/date=2026-09-04/run_id={run_id}"
            yield f"{base}/run.json"
            yield f"{base}/summary.json"

    async def get_json(self, key: str) -> dict[str, Any]:
        runtime_mode = "apply" if str(RUN_APPLY) in key else "observe"
        schema_version = 2 if runtime_mode == "apply" else 3

        return {
            "event_type": "hh.batch.started",
            "schema_version": schema_version,
            "runtime_mode": runtime_mode,
        }


class FakeCursor:
    async def fetchall(self) -> list[tuple[UUID]]:
        return [(RUN_DONE,)]


class FakeConnection:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def execute(self, query: str) -> FakeCursor:
        assert "careerops.observation_runs" in query
        return FakeCursor()

    def transaction(self) -> TransactionRecorder:
        return TransactionRecorder(self.events)


@pytest.mark.asyncio
async def test_materializer_loads_only_pending_finished_observe(
    monkeypatch: Any,
) -> None:
    loaded_ids: list[UUID] = []

    async def fake_load(
        store: Any,
        sink: Any,
        location: Any,
    ) -> LoadedBatchResult:
        del store, sink
        loaded_ids.append(location.run_id)

        return LoadedBatchResult(
            run_id=location.run_id,
            complete=True,
            candidates=25,
            decisions=0,
            applications=0,
        )

    monkeypatch.setattr(
        materializer,
        "load_hh_batch",
        fake_load,
    )

    connection = FakeConnection()

    report = await materializer.run_materializer(
        FakeStore(),
        connection,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert report.discovered == 3
    assert report.eligible == 2
    assert report.pending == 1
    assert report.selected == 1
    assert loaded_ids == [RUN_PENDING]
    assert connection.events == ["begin", "commit"]
    assert len(report.loaded) == 1
    assert not report.failures
