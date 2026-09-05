from __future__ import annotations

import pytest
from support.hh_s3_to_postgres import (
    IdempotentTransactionalSink,
    SnapshotConnection,
    complete_v3_store,
)

from careerops_etl.hh_s3_to_postgres import discover_hh_batches
from scripts.backfill_hh_postgres import load_hh_run_transactionally


@pytest.mark.asyncio
async def test_v3_replay_of_same_run_creates_zero_duplicate_oltp_rows() -> None:
    store = complete_v3_store()
    location = (await discover_hh_batches(store))[0]
    sink = IdempotentTransactionalSink()
    connection = SnapshotConnection(sink)

    first = await load_hh_run_transactionally(
        store,
        sink,
        connection,
        location,
    )
    counts_after_first = sink.row_counts
    second = await load_hh_run_transactionally(
        store,
        sink,
        connection,
        location,
    )

    assert first == second
    assert counts_after_first == (1, 2, 1, 1, 1, 2)
    assert sink.row_counts == counts_after_first
    assert connection.events == ["begin", "commit", "begin", "commit"]


@pytest.mark.asyncio
async def test_v3_mid_materialization_failure_rolls_back_the_whole_run() -> None:
    store = complete_v3_store()
    location = (await discover_hh_batches(store))[0]
    sink = IdempotentTransactionalSink(fail_on_evaluation_attempt=2)
    connection = SnapshotConnection(sink)

    with pytest.raises(
        RuntimeError,
        match="injected evaluation materialization failure",
    ):
        await load_hh_run_transactionally(
            store,
            sink,
            connection,
            location,
        )

    assert connection.events == ["begin", "rollback"]
    assert sink.row_counts == (0, 0, 0, 0, 0, 0)
