"""Asynchronous CLI for the HH S3 RAW to PostgreSQL OLTP backfill."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Any, TextIO
from uuid import UUID

from careerops_etl.hh_s3_to_postgres import (
    LoadedBatchResult,
    discover_hh_batches,
    load_hh_batch,
)
from careerops_storage import (
    PostgresOLTPStore,
    PostgresSettings,
    S3JsonStore,
    S3Settings,
    connect_postgres,
)


@dataclass(frozen=True, slots=True)
class BackfillFailure:
    """Concise information about one batch rolled back by the CLI."""

    run_id: UUID
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class BackfillReport:
    """Aggregate successes and failures from one backfill invocation."""

    discovered: int
    selected: int
    loaded: tuple[LoadedBatchResult, ...]
    failures: tuple[BackfillFailure, ...]


async def load_hh_run_transactionally(
    store: Any,
    sink: Any,
    conn: Any,
    location: Any,
) -> LoadedBatchResult:
    """Materialize exactly one RAW run inside exactly one database transaction."""

    async with conn.transaction():
        return await load_hh_batch(store, sink, location)


async def run_backfill(
    store: Any,
    conn: Any,
    *,
    limit: int | None = None,
    run_id: UUID | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> BackfillReport:
    """Load selected batches with one independent PostgreSQL transaction each."""

    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")
    if limit is not None and run_id is not None:
        raise ValueError("limit and run_id are mutually exclusive")

    batches = await discover_hh_batches(store)
    if run_id is not None:
        selected = [batch for batch in batches if batch.run_id == run_id]
        if not selected:
            raise ValueError(f"run_id {run_id} was not found in S3")
    else:
        selected = batches[:limit] if limit is not None else batches
    sink = PostgresOLTPStore(conn)
    loaded: list[LoadedBatchResult] = []
    failures: list[BackfillFailure] = []

    for location in selected:
        try:
            result = await load_hh_run_transactionally(
                store,
                sink,
                conn,
                location,
            )
        except Exception as exc:  # noqa: BLE001 - isolate and report one failed batch
            failure = BackfillFailure(
                run_id=location.run_id,
                error_type=type(exc).__name__,
                message=str(exc),
            )
            failures.append(failure)
            print(
                f"ERROR run_id={failure.run_id} "
                f"{failure.error_type}: {failure.message}",
                file=stderr,
            )
            continue

        loaded.append(result)
        status = "finished" if result.complete else "incomplete"
        print(
            f"OK run_id={result.run_id} status={status} "
            f"candidates={result.candidates} decisions={result.decisions} "
            f"applications={result.applications}",
            file=stdout,
        )

    report = BackfillReport(
        discovered=len(batches),
        selected=len(selected),
        loaded=tuple(loaded),
        failures=tuple(failures),
    )
    print(
        f"SUMMARY discovered={report.discovered} selected={report.selected} "
        f"loaded={len(report.loaded)} failed={len(report.failures)}",
        file=stdout,
    )
    return report


def _parser() -> argparse.ArgumentParser:
    """Build the command-line parser for bounded backfill execution."""

    parser = argparse.ArgumentParser(
        description="Backfill CareerOPS HH SeaweedFS RAW into PostgreSQL OLTP"
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--limit",
        type=int,
        help="Process at most the earliest N discovered batches",
    )
    selection.add_argument(
        "--run-id",
        type=UUID,
        help="Process exactly one discovered batch with this UUID",
    )
    return parser


async def _async_main() -> int:
    """Open async storage clients and execute the requested backfill."""

    parser = _parser()
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")

    async with S3JsonStore(S3Settings.from_env()) as store:
        async with await connect_postgres(PostgresSettings.from_env()) as conn:
            report = await run_backfill(
                store,
                conn,
                limit=args.limit,
                run_id=args.run_id,
            )
    return 1 if report.failures else 0


def main() -> int:
    """Run the async CLI, selecting a psycopg-compatible loop on Windows."""

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
