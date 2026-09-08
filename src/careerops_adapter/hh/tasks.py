"""Persistent PostgreSQL v2 source tasks, которыми владеет HH adapter"""

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
    """Исполняемые виды HH work из схемы careerops_v2.source_tasks"""

    SEARCH_PAGE = "search_page"
    VACANCY_FETCH = "vacancy_fetch"
    RESUME_SYNC = "resume_sync"
    RESUME_FETCH = "resume_fetch"


class SourceTaskStatus(StrEnum):
    """Operational states, общие с careerops_v2.source_tasks"""

    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    DEFERRED = "deferred"
    RETRYABLE_FAILURE = "retryable_failure"
    SUCCEEDED = "succeeded"
    TERMINAL_FAILURE = "terminal_failure"
    CANCELLED = "cancelled"


class SourceTaskLeaseLost(RuntimeError):
    """Worker больше не владеет live lease для source task"""


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
                raise ValueError(
                    f"secret-like source task parameter is forbidden: {path}.{key}"
                )
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


def _profile_key(parameters: dict[str, Any]) -> str:
    value = parameters.get("profile_key")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("HH source task parameters require a non-empty profile_key")
    return value.strip()


@dataclass(frozen=True, slots=True)
class SourceTaskSpec:
    """Immutable identity и параметры task до enqueue в PostgreSQL"""

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
        _profile_key(normalized)
        return cls(
            kind=kind,
            parameters=normalized,
            task_key=_task_key(kind, normalized),
            parent_task_id=parent_task_id,
        )


@dataclass(frozen=True, slots=True)
class SourceTaskRecord:
    """Claimed source task со стабильным HH account registry key"""

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
    max_pages: int,
    parent_task_id: UUID | None = None,
) -> SourceTaskSpec:
    """Создаёт idempotent query-page task внутри observation generation"""

    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")
    if page < 0 or page >= max_pages:
        raise ValueError("page must be inside the configured search page window")
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
            "max_pages": max_pages,
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
    """Создаёт generation-scoped task загрузки full vacancy"""

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
    """Создаёт task одного точного page /resumes/mine для sync generation"""

    if page < 0:
        raise ValueError("page must be >= 0")
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
    """Создаёт generation-scoped task загрузки full resume"""

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
    """SQL owner механики очереди careerops_v2.source_tasks

    Queue transitions являются независимыми transactional facts
    Repository требует отдельное autocommit connection
    Операция, которая должна атомарно сохранить children и успех parent task,
    сама открывает explicit transaction
    """

    def __init__(self, conn: AsyncConnection[Any]) -> None:
        if not conn.autocommit:
            raise ValueError("SourceTaskRepository requires an autocommit PostgreSQL connection")
        self._conn = conn

    async def enqueue(self, *, account_id: int, spec: SourceTaskSpec) -> UUID:
        """Добавляет task один раз и не сбрасывает существующий state"""

        if account_id <= 0:
            raise ValueError("account_id must be positive")
        profile_key = _profile_key(spec.parameters)
        task_id = uuid4()
        cursor = await self._conn.execute(
            """
            INSERT INTO careerops_v2.source_tasks (
                id,
                account_id,
                task_key,
                task_kind,
                parent_task_id,
                parameters
            )
            SELECT %s, a.id, %s, %s, %s, %s
            FROM careerops_v2.accounts AS a
            JOIN careerops_v2.sources AS s ON s.id = a.source_id
            JOIN careerops_v2.profiles AS p
              ON p.account_id = a.id AND p.source_id = a.source_id
            WHERE a.id = %s
              AND s.source_key = 'hh'
              AND p.profile_key = %s
            ON CONFLICT (account_id, task_key)
            DO UPDATE SET task_key = EXCLUDED.task_key
            RETURNING id
            """,
            (
                task_id,
                spec.task_key,
                spec.kind.value,
                spec.parent_task_id,
                Jsonb(spec.parameters),
                account_id,
                profile_key,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(
                "account/profile is not a registered HH profile: "
                f"account_id={account_id}, profile_key={profile_key!r}"
            )
        return UUID(str(row[0]))

    async def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 300,
        account_id: int | None = None,
    ) -> SourceTaskRecord | None:
        """Claim одной due task или reclaim expired lease через SKIP LOCKED fencing"""

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
                JOIN careerops_v2.accounts AS a ON a.id = st.account_id
                JOIN careerops_v2.sources AS s ON s.id = a.source_id
                WHERE s.source_key = 'hh'
                  AND st.task_kind IN (
                      'search_page', 'vacancy_fetch', 'resume_sync', 'resume_fetch'
                  )
                  AND (
                      (
                          st.status IN ('pending', 'deferred', 'retryable_failure')
                          AND st.next_attempt_at <= now()
                      )
                      OR (
                          st.status IN ('claimed', 'running')
                          AND st.lease_expires_at <= now()
                      )
                  )
                  AND (%s IS NULL OR st.account_id = %s)
                ORDER BY
                    CASE
                        WHEN st.status IN ('claimed', 'running') THEN st.lease_expires_at
                        ELSE st.next_attempt_at
                    END,
                    st.id
                FOR UPDATE OF st SKIP LOCKED
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
                finished_at = NULL,
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
        """Переводит свежую claimed task в running при живом lease"""

        token = self._required_token(task)
        cursor = await self._conn.execute(
            """
            UPDATE careerops_v2.source_tasks
            SET status = 'running', updated_at = now()
            WHERE id = %s
              AND lease_token = %s
              AND status = 'claimed'
              AND lease_expires_at > now()
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
        """Сохраняет child work до атомарного подтверждения parent task"""

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
                  AND lease_expires_at > now()
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
        """Откладывает throttled/CAPTCHA work без потери task"""

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
        """Освобождает временно неуспешную task для следующей попытки"""

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
        """Фиксирует unrecoverable source error при живом lease"""

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
              AND lease_expires_at > now()
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
              AND lease_expires_at > now()
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
