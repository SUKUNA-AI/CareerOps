"""Materialize completed schema-v3 HH OBSERVE RAW batches into PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Any, TextIO
from uuid import UUID

from careerops_etl.hh_s3_to_postgres import (
    HHBatchLocation,
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
class MaterializationFailure:
    run_id: UUID
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class MaterializationReport:
    discovered: int
    eligible: int
    pending: int
    selected: int
    loaded: tuple[LoadedBatchResult, ...]
    failures: tuple[MaterializationFailure, ...]


async def _materialized_observation_run_ids(conn: Any) -> set[UUID]:
    cursor = await conn.execute(
        "SELECT id FROM careerops.observation_runs"
    )
    rows = await cursor.fetchall()
    return {UUID(str(row[0])) for row in rows}


async def _eligible_observe_batches(
    store: Any,
    batches: list[HHBatchLocation],
) -> list[HHBatchLocation]:
    eligible: list[HHBatchLocation] = []

    for location in batches:
        # Never materialize a run that is still being written.
        if not location.has_summary:
            continue

        payload = await store.get_json(location.run_key)
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"{location.run_key} must contain a JSON object"
            )

        if (
            payload.get("event_type") == "hh.batch.started"
            and payload.get("schema_version") == 3
            and payload.get("runtime_mode") == "observe"
        ):
            eligible.append(location)

    return eligible


async def run_materializer(
    store: Any,
    conn: Any,
    *,
    limit: int = 10,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> MaterializationReport:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    batches = await discover_hh_batches(store)
    eligible = await _eligible_observe_batches(store, batches)

    materialized_ids = await _materialized_observation_run_ids(conn)
    pending = [
        location
        for location in eligible
        if location.run_id not in materialized_ids
    ]
    selected = pending[:limit]

    sink = PostgresOLTPStore(conn)
    loaded: list[LoadedBatchResult] = []
    failures: list[MaterializationFailure] = []

    for location in selected:
        try:
            async with conn.transaction():
                result = await load_hh_batch(
                    store,
                    sink,
                    location,
                )
        except Exception as exc:  # noqa: BLE001
            failure = MaterializationFailure(
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
        print(
            f"OK run_id={result.run_id} "
            f"status={'finished' if result.complete else 'incomplete'} "
            f"candidates={result.candidates} "
            f"decisions={result.decisions} "
            f"applications={result.applications}",
            file=stdout,
        )

    report = MaterializationReport(
        discovered=len(batches),
        eligible=len(eligible),
        pending=len(pending),
        selected=len(selected),
        loaded=tuple(loaded),
        failures=tuple(failures),
    )

    print(
        "SUMMARY "
        f"discovered={report.discovered} "
        f"eligible={report.eligible} "
        f"pending={report.pending} "
        f"selected={report.selected} "
        f"loaded={len(report.loaded)} "
        f"failed={len(report.failures)}",
        file=stdout,
    )

    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize pending CareerOPS HH schema-v3 OBSERVE RAW "
            "batches into PostgreSQL"
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum pending completed OBSERVE runs per invocation",
    )
    return parser


async def _async_main() -> int:
    args = _parser().parse_args()
    if args.limit < 1:
        raise ValueError("--limit must be >= 1")

    async with S3JsonStore(S3Settings.from_env()) as store:
        async with await connect_postgres(PostgresSettings.from_env()) as conn:
            report = await run_materializer(
                store,
                conn,
                limit=args.limit,
            )

    return 1 if report.failures else 0


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
