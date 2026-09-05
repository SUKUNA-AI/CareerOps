"""Persistent PostgreSQL v2 source-task contracts owned by the HH adapter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb


class SourceTaskKind(StrEnum):
    """HH work kinds admitted by the PostgreSQL v2 source_tasks schema."""

    SEARCH = "search"
    SEARCH_PAGE = "search_page"
    VACANCY_FETCH = "vacancy_fetch"
    RESUME_SYNC = "resume_sync"
    RESUME_FETCH = "resume_fetch"


class SourceTaskStatus(StrEnum):
    """Operational states shared with careerops_v2.source_tasks."""

    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    DEFERRED = "deferred"
    RETRYABLE_FAILURE = "retryable_failure"
    SUCCEEDED = "succeeded"
    TERMINAL_FAILURE = "terminal_failure"
    CANCELLED = "cancelled"


class SourceTaskLeaseLost(RuntimeError):
    """Report a stale worker that no longer owns the source-task lease."""


_FORBIDDEN_PARAMETER_KEYS = (
    "authorization",
    "access_key",
    "secret",
    "password",
    "cookie",
    "xsrf",
    "refresh_token",
    "access_token",
)


def _reject_secret_keys(value: Any, path: str = "parameters") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).casefold()
            if any(fragment in normalized for fragment in _FORBIDDEN_PARAMETER_KEYS):
                raise ValueError(f"secret-like source task parameter is forbidden: {path}.{key}")
            _reject_secret_keys(nested, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_secret_keys(nested, f"{path}[{index}]")


def _canonical_parameters(parameters: dict[str, Any]) -> bytes:
    _reject_secret_keys(parameters)
    try:
        encoded = json.dumps(
            parameters,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("source task parameters must be JSON-serializable") from exc
    if len(encoded) > 16384:
        raise ValueError("source task parameters exceed the 16 KiB PostgreSQL contract")
    return encoded


def _task_key(kind: SourceTaskKind, parameters: dict[str, Any]) -> str:
    envelope = b"careerops-hh-source-task-v1\0" + kind.value.encode("ascii") + b"\0"
    digest = hashlib.sha256(envelope + _canonical_parameters(parameters)).hexdigest()
    return f"{kind.value}:v1:{digest}"


@dataclass(frozen=True, slots=True)
class SourceTaskSpec:
    """Immutable task identity and parameters before PostgreSQL enqueue."""

    kind: SourceTaskKind
    parameters: dict[str, Any]
    task_key: str
    parent_task_id: UUID | None = None

    @classmethod
    def build(
        cls,
        kind: SourceTaskKind,
        parameters: dict[str, Any],
        *,
        parent_task_id: UUID | None = None,
    ) -> SourceTaskSpec:
        normalized = json.loads(_canonical_parameters(parameters).decode("utf-8"))
        if not isinstance(normalized, dict):
            raise ValueError("source task parameters must be a JSON object")
        return cls(
            kind=kind,
            parameters=normalized,
            task_key=_task_key(kind, normalized),
            parent_task_id=parent_task_id,
        )


@dataclass(frozen=True, slots=True)
class SourceTaskRecord:
    """One claimed or persisted source task resolved with its account registry key."""

    id: UUID
    account_id: int
    account_key: str
    task_key: str
    kind: SourceTaskKind
    parent_task_id: UUID | None
    parameters: dict[str, Any]
    status: SourceTaskStatus
    attempt_count: int
    lease_token: UUID | None


def search_page_task(
    *,
    generation_id: UUID,
    profile_key: str,
    query_key: str,
    text: str,
    page: int,
    area: int = 1,
    period: int = 14,
    order_by: str = "publication_time",
    per_page: int = 50,
    professional_roles: tuple[int, ...] = (),
    parent_task_id: UUID | None = None,
) -> SourceTaskSpec:
    """Build one idempotent query-page task within an observation generation."""

    return SourceTaskSpec.build(
        SourceTaskKind.SEARCH_PAGE,
        {
            "generation_id": str(generation_id),
            "profile_key": profile_key,
            "query_key": query_key,
            "text": text,
            "page": page,
            "area": area,
            "period": period,
            "order_by": order_by,
            "per_page": per_page,
            "professional_roles": list(professional_roles),
        },
        parent_task_id=parent_task_id,
    )


def vacancy_fetch_task(
    *,
    generation_id: UUID,
    profile_key: str,
    vacancy_id: str,
    parent_task_id: UUID | None = None,
) -> SourceTaskSpec:
    """Build a generation-scoped full-vacancy fetch task."""

    return SourceTaskSpec.build(
        SourceTaskKind.VACANCY_FETCH,
        {
            "generation_id": str(generation_id),
            "profile_key": profile_key,
            "vacancy_id": vacancy_id,
        },
        parent_task_id=parent_task_id,
    )


def resume_sync_task(
    *,
    generation_id: UUID,
    profile_key: str,
    page: int = 0,
    parent_task_id: UUID | None = None,
) -> SourceTaskSpec:
    """Build one exact /resumes/mine page task for a sync generation."""

    return SourceTaskSpec.build(
        SourceTaskKind.RESUME_SYNC,
        {
            "generation_id": str(generation_id),
            "profile_key": profile_key,
            "page": page,
            "per_page": 100,
        },
        parent_task_id=parent_task_id,
    )


def resume_fetch_task(
    *,
    generation_id: UUID,
    profile_key: str,
    resume_id: str,
    parent_task_id: UUID | None = None,
) -> SourceTaskSpec:
    """Build one generation-scoped full-resume fetch task."""

    return SourceTaskSpec.build(
        SourceTaskKind.RESUME_FETCH,
        {
            "generation_id": str(generation_id),
            "profile_key": profile_key,
            "resume_id": resume_id,
        },
        parent_task_id=parent_task_id,
    )


class SourceTaskRepository:
    """Explicit SQL owner for careerops_v2.source_tasks queue mechanics."""

    def __init__(self, conn: AsyncConnection[Any]) -> None:
        self._conn = conn

    async def enqueue(self, *, account_id: int, spec: SourceTaskSpec) -> UUID:
        """Insert once by natural task key without resetting an existing task."""

        if account_id <= 0:
            raise ValueError("account_id must be positive")
        task_id = uuid4()
        cursor = await self._conn.execute(
            """
            WITH inserted AS (
                INSERT INTO careerops_v2.source_tasks (
                    id,
                    account_id,
                    task_key,
                    task_kind,
                    parent_task_id,
                    parameters
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (account_id, task_key) DO NOTHING
                RETURNING id
            )
            SELECT id FROM inserted
            UNION ALL
            SELECT id
            FROM careerops_v2.source_tasks
            WHERE account_id = %s AND task_key = %s
            LIMIT 1
            """,
            (
                task_id,
                account_id,
                spec.task_key,
                spec.kind.value,
                spec.parent_task_id,
                Jsonb(spec.parameters),
                account_id,
                spec.task_key,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("source task enqueue returned no task id")
        return UUID(str(row[0]))

    async def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 300,
        account_id: int | None = None,
    ) -> SourceTaskRecord | None:
        """Atomically claim one due task using SKIP LOCKED and a fencing token."""

        normalized_worker = worker_id.strip()
        if not normalized_worker:
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if account_id is not None and account_id <= 0:
            raise ValueError("account_id must be positive")

        lease_token = uuid4()
        cursor = await self._conn.execute(
            """
            WITH candidate AS (
                SELECT st.id
                FROM careerops_v2.source_tasks AS st
                WHERE st.status IN ('pending', 'deferred', 'retryable_failure')
                  AND st.next_attempt_at <= now()
                  AND (%s IS NULL OR st.account_id = %s)
                ORDER BY st.next_attempt_at, st.id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE careerops_v2.source_tasks AS st
            SET status = 'claimed',
                attempt_count = st.attempt_count + 1,
                next_attempt_at = NULL,
                lease_owner = %s,
                lease_token = %s,
                leased_at = now(),
                lease_expires_at = now() + make_interval(secs => %s),
                error_category = NULL,
                result_artifact_uri = NULL,
                updated_at = now()
            FROM candidate AS c, careerops_v2.accounts AS a
            WHERE st.id = c.id AND a.id = st.account_id
            RETURNING
                st.id,
                st.account_id,
                a.account_key,
                st.task_key,
                st.task_kind,
                st.parent_task_id,
                st.parameters,
                st.status,
                st.attempt_count,
                st.lease_token
            """,
            (account_id, account_id, normalized_worker, lease_token, lease_seconds),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        parameters = row[6]
        if not isinstance(parameters, dict):
            raise TypeError("source task parameters returned by PostgreSQL are not an object")
        return SourceTaskRecord(
            id=UUID(str(row[0])),
            account_id=int(row[1]),
            account_key=str(row[2]),
            task_key=str(row[3]),
            kind=SourceTaskKind(str(row[4])),
            parent_task_id=UUID(str(row[5])) if row[5] is not None else None,
            parameters=parameters,
            status=SourceTaskStatus(str(row[7])),
            attempt_count=int(row[8]),
            lease_token=UUID(str(row[9])) if row[9] is not None else None,
        )

    async def mark_running(self, task: SourceTaskRecord) -> None:
        """Move a freshly claimed task to running under the same fencing token."""

        token = self._required_token(task)
        cursor = await self._conn.execute(
            """
            UPDATE careerops_v2.source_tasks
            SET status = 'running', updated_at = now()
            WHERE id = %s AND lease_token = %s AND status = 'claimed'
            """,
            (task.id, token),
        )
        self._require_one(cursor.rowcount, task.id)

    async def succeed_with_children(
        self,
        task: SourceTaskRecord,
        *,
        result_artifact_uri: str,
        children: tuple[SourceTaskSpec, ...] = (),
    ) -> None:
        """Persist child work before atomically acknowledging the parent task."""

        if not result_artifact_uri.startswith("s3://"):
            raise ValueError("source task success requires an s3:// result URI")
        token = self._required_token(task)
        async with self._conn.transaction():
            for child in children:
                await self.enqueue(account_id=task.account_id, spec=child)
            cursor = await self._conn.execute(
                """
                UPDATE careerops_v2.source_tasks
                SET status = 'succeeded',
                    result_artifact_uri = %s,
                    finished_at = now(),
                    lease_owner = NULL,
                    lease_token = NULL,
                    leased_at = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE id = %s
                  AND lease_token = %s
                  AND status IN ('claimed', 'running')
                """,
                (result_artifact_uri, task.id, token),
            )
            self._require_one(cursor.rowcount, task.id)

    async def defer(
        self,
        task: SourceTaskRecord,
        *,
        error_category: str,
        next_attempt_at: datetime,
    ) -> None:
        """Defer throttled/quota/CAPTCHA work without discarding the task."""

        await self._release_retryable(
            task,
            status=SourceTaskStatus.DEFERRED,
            error_category=error_category,
            next_attempt_at=next_attempt_at,
        )

    async def retryable_failure(
        self,
        task: SourceTaskRecord,
        *,
        error_category: str,
        next_attempt_at: datetime,
    ) -> None:
        """Release a transiently failed task for a later attempt."""

        await self._release_retryable(
            task,
            status=SourceTaskStatus.RETRYABLE_FAILURE,
            error_category=error_category,
            next_attempt_at=next_attempt_at,
        )

    async def terminal_failure(
        self,
        task: SourceTaskRecord,
        *,
        error_category: str,
    ) -> None:
        """Record an explicit unrecoverable source error under the current lease."""

        normalized_error = error_category.strip()
        if not normalized_error:
            raise ValueError("error_category must not be empty")
        token = self._required_token(task)
        cursor = await self._conn.execute(
            """
            UPDATE careerops_v2.source_tasks
            SET status = 'terminal_failure',
                next_attempt_at = NULL,
                finished_at = now(),
                error_category = %s,
                result_artifact_uri = NULL,
                lease_owner = NULL,
                lease_token = NULL,
                leased_at = NULL,
                lease_expires_at = NULL,
                updated_at = now()
            WHERE id = %s
              AND lease_token = %s
              AND status IN ('claimed', 'running')
            """,
            (normalized_error, task.id, token),
        )
        self._require_one(cursor.rowcount, task.id)

    async def _release_retryable(
        self,
        task: SourceTaskRecord,
        *,
        status: SourceTaskStatus,
        error_category: str,
        next_attempt_at: datetime,
    ) -> None:
        if status not in {SourceTaskStatus.DEFERRED, SourceTaskStatus.RETRYABLE_FAILURE}:
            raise ValueError("retryable release requires deferred or retryable_failure")
        normalized_error = error_category.strip()
        if not normalized_error:
            raise ValueError("error_category must not be empty")
        if next_attempt_at.tzinfo is None or next_attempt_at.utcoffset() is None:
            raise ValueError("next_attempt_at must be timezone-aware")
        token = self._required_token(task)
        cursor = await self._conn.execute(
            """
            UPDATE careerops_v2.source_tasks
            SET status = %s,
                next_attempt_at = %s,
                finished_at = NULL,
                error_category = %s,
                result_artifact_uri = NULL,
                lease_owner = NULL,
                lease_token = NULL,
                leased_at = NULL,
                lease_expires_at = NULL,
                updated_at = now()
            WHERE id = %s
              AND lease_token = %s
              AND status IN ('claimed', 'running')
            """,
            (status.value, next_attempt_at, normalized_error, task.id, token),
        )
        self._require_one(cursor.rowcount, task.id)

    @staticmethod
    def _required_token(task: SourceTaskRecord) -> UUID:
        if task.lease_token is None:
            raise SourceTaskLeaseLost(f"source task {task.id} has no lease token")
        return task.lease_token

    @staticmethod
    def _require_one(rowcount: int, task_id: UUID) -> None:
        if rowcount != 1:
            raise SourceTaskLeaseLost(f"source task lease lost for {task_id}")
