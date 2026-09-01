from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from io import StringIO
from types import TracebackType
from typing import Any
from uuid import UUID

import pytest

import scripts.backfill_hh_postgres as cli
from careerops_etl.hh_s3_to_postgres import LoadedBatchResult

RUN_1 = UUID("11111111-1111-4111-8111-111111111111")
RUN_2 = UUID("22222222-2222-4222-8222-222222222222")


class FakeStore:
    async def iter_keys(self, prefix: str = ""):
        assert prefix == "batches"
        yield f"batches/date=2026-08-30/run_id={RUN_1}/run.json"
        yield f"batches/date=2026-08-31/run_id={RUN_2}/run.json"


class FakeTransaction(AbstractAsyncContextManager[None]):
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> None:
        self.connection.events.append("begin")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.connection.events.append("rollback" if exc_type else "commit")
        return False


class FakeConnection:
    def __init__(self) -> None:
        self.events: list[str] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)


@pytest.mark.asyncio
async def test_each_batch_has_an_independent_transaction(monkeypatch: Any) -> None:
    calls: list[UUID] = []

    async def fake_load(store: Any, sink: Any, location: Any) -> LoadedBatchResult:
        calls.append(location.run_id)
        if location.run_id == RUN_2:
            raise RuntimeError("broken audit")
        return LoadedBatchResult(
            run_id=location.run_id,
            complete=True,
            candidates=2,
            decisions=1,
            applications=1,
        )

    monkeypatch.setattr(cli, "load_hh_batch", fake_load)
    conn = FakeConnection()
    stdout = StringIO()
    stderr = StringIO()

    report = await cli.run_backfill(
        FakeStore(),
        conn,
        stdout=stdout,
        stderr=stderr,
    )

    assert calls == [RUN_1, RUN_2]
    assert conn.events == ["begin", "commit", "begin", "rollback"]
    assert len(report.loaded) == 1
    assert len(report.failures) == 1
    assert f"OK run_id={RUN_1}" in stdout.getvalue()
    assert f"ERROR run_id={RUN_2}" in stderr.getvalue()


@pytest.mark.asyncio
async def test_limit_selects_only_earliest_batches(monkeypatch: Any) -> None:
    calls: list[UUID] = []

    async def fake_load(store: Any, sink: Any, location: Any) -> LoadedBatchResult:
        calls.append(location.run_id)
        return LoadedBatchResult(
            run_id=location.run_id,
            complete=False,
            candidates=0,
            decisions=0,
            applications=0,
        )

    monkeypatch.setattr(cli, "load_hh_batch", fake_load)
    report = await cli.run_backfill(
        FakeStore(),
        FakeConnection(),
        limit=1,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert calls == [RUN_1]
    assert report.discovered == 2
    assert report.selected == 1


@pytest.mark.asyncio
async def test_run_id_selects_exactly_one_requested_batch(monkeypatch: Any) -> None:
    calls: list[UUID] = []

    async def fake_load(store: Any, sink: Any, location: Any) -> LoadedBatchResult:
        calls.append(location.run_id)
        return LoadedBatchResult(
            run_id=location.run_id,
            complete=True,
            candidates=1,
            decisions=1,
            applications=0,
        )

    monkeypatch.setattr(cli, "load_hh_batch", fake_load)
    connection = FakeConnection()

    report = await cli.run_backfill(
        FakeStore(),
        connection,
        run_id=RUN_2,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert calls == [RUN_2]
    assert connection.events == ["begin", "commit"]
    assert report.discovered == 2
    assert report.selected == 1
    assert [result.run_id for result in report.loaded] == [RUN_2]


@pytest.mark.asyncio
async def test_run_id_and_limit_are_rejected_by_runtime_validation() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        await cli.run_backfill(
            FakeStore(),
            FakeConnection(),
            limit=1,
            run_id=RUN_1,
            stdout=StringIO(),
            stderr=StringIO(),
        )


def test_run_id_and_limit_are_mutually_exclusive_cli_options() -> None:
    parser = cli._parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--limit", "1", "--run-id", str(RUN_1)])
