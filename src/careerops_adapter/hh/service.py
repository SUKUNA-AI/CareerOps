"""Compose one account-scoped HH source worker from v2 runtime dependencies.

This module intentionally stops at source ingestion. It does not choose search
queries, schedule runs, materialize domain state, filter vacancies, or submit
applications. Orchestration only has to enqueue persistent source tasks and invoke
this bounded worker.
"""

from __future__ import annotations

import hashlib
import os
import socket
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import AsyncConnection

from careerops_integrations.hh.configuration import (
    HHAccountConfig,
    HHAccountsConfig,
    HHConfigError,
    accounts_config_path_from_env,
    load_accounts_config,
)
from careerops_integrations.hh.driver import HHApplicantToolCLI
from careerops_integrations.hh.runtime import HHExternalWriteGuard, RuntimeMode
from careerops_storage.s3 import S3JsonStore, S3Settings

from .errors import HHFailureDisposition
from .raw import HHRawPublisher
from .tasks import SourceTaskRecord, SourceTaskRepository
from .transport import HHApplicantToolTransport
from .worker import (
    HHSourceFailurePolicy,
    HHSourceTaskExecutor,
    SourceTaskRunOutcome,
)


@dataclass(frozen=True, slots=True)
class V2PostgresSettings:
    """Explicit v2 PostgreSQL target; never fall back to the legacy runtime DSN."""

    dsn: str

    @classmethod
    def from_env(cls) -> V2PostgresSettings:
        value = os.getenv("CAREEROPS_V2_POSTGRES_DSN", "").strip()
        if not value:
            raise RuntimeError("CAREEROPS_V2_POSTGRES_DSN is required by the HH v2 worker")
        legacy = os.getenv("CAREEROPS_POSTGRES_DSN", "").strip()
        if legacy and legacy == value:
            raise RuntimeError(
                "CAREEROPS_V2_POSTGRES_DSN must not point at the legacy CareerOPS database"
            )
        return cls(dsn=value)


@dataclass(frozen=True, slots=True)
class HHAccountWorkerSummary:
    """Small machine-readable result for one bounded account worker invocation."""

    account_key: str
    profile_key: str
    worker_id: str
    lock_acquired: bool
    claimed: int = 0
    succeeded: int = 0
    deferred: int = 0
    retryable_failure: int = 0
    terminal_failure: int = 0
    account_blocked: bool = False
    account_deferred_tasks: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_worker_id() -> str:
    """Return a diagnostic worker id without embedding credentials or source data."""

    return f"{socket.gethostname()}:{os.getpid()}"


def default_accounts_config_path() -> Path:
    """Expose the existing non-secret account topology pointer to the new CLI."""

    return accounts_config_path_from_env()


def _resolve_source_account(accounts: HHAccountsConfig, account_key: str) -> HHAccountConfig:
    """Resolve an enabled source account without depending on resume policy bindings."""

    normalized = account_key.strip()
    if not normalized:
        raise ValueError("account_key must not be empty")
    for account in accounts.enabled_accounts:
        if account.key == normalized:
            return account
    raise HHConfigError(f"enabled HH source account not found: {normalized!r}")


async def _resolve_v2_account_id(
    conn: AsyncConnection[Any],
    *,
    account_key: str,
    profile_key: str,
) -> int:
    cursor = await conn.execute(
        """
        SELECT a.id
        FROM careerops_v2.accounts AS a
        JOIN careerops_v2.sources AS s ON s.id = a.source_id
        JOIN careerops_v2.profiles AS p
          ON p.account_id = a.id AND p.source_id = a.source_id
        WHERE s.source_key = 'hh'
          AND a.account_key = %s
          AND p.profile_key = %s
        """,
        (account_key, profile_key),
    )
    rows = await cursor.fetchall()
    if len(rows) != 1:
        raise RuntimeError(
            "expected exactly one v2 HH account/profile mapping for "
            f"account={account_key!r}, profile={profile_key!r}; found {len(rows)}"
        )
    return int(rows[0][0])


def _advisory_lock_key(account_key: str) -> int:
    digest = hashlib.blake2b(
        b"careerops-hh-source-worker\0" + account_key.encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


async def _try_lock_account(conn: AsyncConnection[Any], account_key: str) -> tuple[int, bool]:
    key = _advisory_lock_key(account_key)
    cursor = await conn.execute("SELECT pg_try_advisory_lock(%s)", (key,))
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL advisory lock query returned no row")
    return key, bool(row[0])


async def _unlock_account(conn: AsyncConnection[Any], key: int) -> None:
    cursor = await conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
    row = await cursor.fetchone()
    if row is None or row[0] is not True:
        raise RuntimeError("HH source worker lost its account advisory lock")


async def _defer_account_after_block(
    conn: AsyncConnection[Any],
    *,
    account_id: int,
    error_category: str,
    next_attempt_at: datetime,
) -> int:
    """Persist an account-wide auth/session pause using existing source-task state.

    We intentionally avoid adding scheduler state to the accounts table. Ready work
    and expired leases are deferred to the same boundary. A currently live lease is
    never stolen.
    """

    cursor = await conn.execute(
        """
        UPDATE careerops_v2.source_tasks
        SET status = 'deferred',
            next_attempt_at = GREATEST(COALESCE(next_attempt_at, %s), %s),
            finished_at = NULL,
            error_category = %s,
            result_artifact_uri = NULL,
            lease_owner = NULL,
            lease_token = NULL,
            leased_at = NULL,
            lease_expires_at = NULL,
            updated_at = now()
        WHERE account_id = %s
          AND task_kind IN ('search_page', 'vacancy_fetch', 'resume_sync', 'resume_fetch')
          AND (
              status IN ('pending', 'deferred', 'retryable_failure')
              OR (
                  status IN ('claimed', 'running')
                  AND lease_expires_at <= now()
              )
          )
        """,
        (next_attempt_at, next_attempt_at, error_category, account_id),
    )
    return int(cursor.rowcount)


def _task_profile_matches(task: SourceTaskRecord, expected_profile: str) -> bool:
    value = task.parameters.get("profile_key")
    return isinstance(value, str) and value.strip() == expected_profile


async def _run_locked_account_worker(
    *,
    conn: AsyncConnection[Any],
    account: HHAccountConfig,
    account_id: int,
    config_dir: Path,
    max_tasks: int,
    lease_seconds: int,
    worker_id: str,
) -> HHAccountWorkerSummary:
    repository = SourceTaskRepository(conn)
    failure_policy = HHSourceFailurePolicy()
    guard = HHExternalWriteGuard(
        runtime_mode=RuntimeMode.OBSERVE,
        allow_external_writes=False,
    )
    driver = HHApplicantToolCLI(
        config_dir=config_dir,
        profile=account.profile,
        external_write_guard=guard,
    )
    transport = HHApplicantToolTransport(driver)

    claimed = 0
    succeeded = 0
    deferred = 0
    retryable_failure = 0
    terminal_failure = 0
    account_blocked = False
    account_deferred_tasks = 0

    async with S3JsonStore(S3Settings.from_env()) as store:
        executor = HHSourceTaskExecutor(
            transport=transport,
            raw=HHRawPublisher(store),
            repository=repository,
            failure_policy=failure_policy,
        )
        for _ in range(max_tasks):
            task = await repository.claim_next(
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                account_id=account_id,
            )
            if task is None:
                break
            claimed += 1

            if not _task_profile_matches(task, account.profile):
                await repository.mark_running(task)
                await repository.terminal_failure(
                    task,
                    error_category="profile_mismatch",
                )
                terminal_failure += 1
                continue

            result = await executor.run(task)
            match result.outcome:
                case SourceTaskRunOutcome.SUCCEEDED:
                    succeeded += 1
                case SourceTaskRunOutcome.DEFERRED:
                    deferred += 1
                case SourceTaskRunOutcome.RETRYABLE_FAILURE:
                    retryable_failure += 1
                case SourceTaskRunOutcome.TERMINAL_FAILURE:
                    terminal_failure += 1

            if result.account_blocked:
                account_blocked = True
                now = datetime.now(UTC)
                blocked_until = failure_policy.next_attempt(
                    disposition=HHFailureDisposition.BLOCK_ACCOUNT,
                    now=now,
                )
                reason = (
                    f"account_block:{result.error_kind.value}"
                    if result.error_kind is not None
                    else "account_block:unknown"
                )
                account_deferred_tasks = await _defer_account_after_block(
                    conn,
                    account_id=account_id,
                    error_category=reason,
                    next_attempt_at=blocked_until,
                )
                break

    return HHAccountWorkerSummary(
        account_key=account.key,
        profile_key=account.profile,
        worker_id=worker_id,
        lock_acquired=True,
        claimed=claimed,
        succeeded=succeeded,
        deferred=deferred,
        retryable_failure=retryable_failure,
        terminal_failure=terminal_failure,
        account_blocked=account_blocked,
        account_deferred_tasks=account_deferred_tasks,
    )


async def run_account_worker(
    *,
    account_key: str,
    accounts_config: Path | None = None,
    config_dir: Path = Path("hh-applicant-tool/config"),
    max_tasks: int = 25,
    lease_seconds: int = 300,
    worker_id: str | None = None,
) -> HHAccountWorkerSummary:
    """Run a bounded, single-account HH source worker against PostgreSQL v2."""

    if max_tasks < 1:
        raise ValueError("max_tasks must be >= 1")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be >= 1")

    config_path = accounts_config or default_accounts_config_path()
    accounts = load_accounts_config(config_path)
    account = _resolve_source_account(accounts, account_key)
    resolved_worker_id = (worker_id or default_worker_id()).strip()
    if not resolved_worker_id:
        raise ValueError("worker_id must not be empty")

    settings = V2PostgresSettings.from_env()
    conn = await psycopg.AsyncConnection.connect(settings.dsn, autocommit=True)
    lock_key: int | None = None
    lock_acquired = False
    try:
        account_id = await _resolve_v2_account_id(
            conn,
            account_key=account.key,
            profile_key=account.profile,
        )
        lock_key, lock_acquired = await _try_lock_account(conn, account.key)
        if not lock_acquired:
            return HHAccountWorkerSummary(
                account_key=account.key,
                profile_key=account.profile,
                worker_id=resolved_worker_id,
                lock_acquired=False,
            )
        return await _run_locked_account_worker(
            conn=conn,
            account=account,
            account_id=account_id,
            config_dir=config_dir,
            max_tasks=max_tasks,
            lease_seconds=lease_seconds,
            worker_id=resolved_worker_id,
        )
    finally:
        try:
            if lock_acquired and lock_key is not None:
                await _unlock_account(conn, lock_key)
        finally:
            await conn.close()
