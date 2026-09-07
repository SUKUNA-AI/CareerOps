"""PostgreSQL implementation of the Processing v2 durable queue contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection

from ..queue import (
    ProcessingJobLeaseLost,
    ProcessingJobRecord,
    ProcessingJobStatus,
    ProcessingPairKey,
    ProcessingWorkSpec,
)


class PostgresProcessingJobStore:
    """Single SQL owner for careerops_v2.processing_jobs lifecycle semantics."""

    def __init__(self, conn: AsyncConnection[Any]) -> None:
        if not conn.autocommit:
            raise ValueError("PostgresProcessingJobStore requires an autocommit connection")
        self._conn = conn

    async def reconcile_current(self, spec: ProcessingWorkSpec) -> UUID:
        """Ensure exact current work exists and fence older active work for the pair."""

        job_id = uuid4()
        async with self._conn.transaction():
            await self._lock_pair(spec.pair)
            await self._cancel_competing_active(spec)

            cursor = await self._conn.execute(
                """
                INSERT INTO careerops_v2.processing_jobs (
                    id,
                    vacancy_id,
                    binding_id,
                    binding_version,
                    input_fingerprint,
                    input_manifest_uri,
                    pipeline_version,
                    policy_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (
                    vacancy_id,
                    binding_id,
                    binding_version,
                    input_fingerprint,
                    pipeline_version,
                    policy_version
                )
                DO UPDATE SET
                    status = CASE
                        WHEN processing_jobs.status = 'cancelled'
                         AND processing_jobs.error_category = 'superseded'
                        THEN 'pending'
                        ELSE processing_jobs.status
                    END,
                    next_attempt_at = CASE
                        WHEN processing_jobs.status = 'cancelled'
                         AND processing_jobs.error_category = 'superseded'
                        THEN now()
                        ELSE processing_jobs.next_attempt_at
                    END,
                    finished_at = CASE
                        WHEN processing_jobs.status = 'cancelled'
                         AND processing_jobs.error_category = 'superseded'
                        THEN NULL
                        ELSE processing_jobs.finished_at
                    END,
                    error_category = CASE
                        WHEN processing_jobs.status = 'cancelled'
                         AND processing_jobs.error_category = 'superseded'
                        THEN NULL
                        ELSE processing_jobs.error_category
                    END,
                    result_artifact_uri = CASE
                        WHEN processing_jobs.status = 'cancelled'
                         AND processing_jobs.error_category = 'superseded'
                        THEN NULL
                        ELSE processing_jobs.result_artifact_uri
                    END,
                    lease_owner = CASE
                        WHEN processing_jobs.status = 'cancelled'
                         AND processing_jobs.error_category = 'superseded'
                        THEN NULL
                        ELSE processing_jobs.lease_owner
                    END,
                    lease_token = CASE
                        WHEN processing_jobs.status = 'cancelled'
                         AND processing_jobs.error_category = 'superseded'
                        THEN NULL
                        ELSE processing_jobs.lease_token
                    END,
                    leased_at = CASE
                        WHEN processing_jobs.status = 'cancelled'
                         AND processing_jobs.error_category = 'superseded'
                        THEN NULL
                        ELSE processing_jobs.leased_at
                    END,
                    lease_expires_at = CASE
                        WHEN processing_jobs.status = 'cancelled'
                         AND processing_jobs.error_category = 'superseded'
                        THEN NULL
                        ELSE processing_jobs.lease_expires_at
                    END,
                    updated_at = now()
                RETURNING id
                """,
                (
                    job_id,
                    spec.vacancy_id,
                    spec.binding_id,
                    spec.binding_version,
                    spec.input_fingerprint,
                    spec.input_manifest_uri,
                    spec.pipeline_version,
                    spec.policy_version,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("processing job upsert returned no id")
            return UUID(str(row[0]))

    async def withdraw_pair(
        self,
        pair: ProcessingPairKey,
        *,
        reason: str,
    ) -> int:
        """Fence every active job for a pair that left authoritative desired state."""

        normalized_reason = self._non_empty(reason, "withdraw reason")
        async with self._conn.transaction():
            await self._lock_pair(pair)
            cursor = await self._conn.execute(
                """
                UPDATE careerops_v2.processing_jobs
                SET status = 'cancelled',
                    next_attempt_at = NULL,
                    finished_at = now(),
                    error_category = %s,
                    result_artifact_uri = NULL,
                    lease_owner = NULL,
                    lease_token = NULL,
                    leased_at = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE vacancy_id = %s
                  AND binding_id = %s
                  AND status IN (
                      'pending', 'claimed', 'running', 'deferred', 'retryable_failure'
                  )
                """,
                (normalized_reason, pair.vacancy_id, pair.binding_id),
            )
            return cursor.rowcount

    async def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> ProcessingJobRecord | None:
        normalized_worker = self._non_empty(worker_id, "worker_id")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        lease_token = uuid4()
        cursor = await self._conn.execute(
            """
            WITH candidate AS (
                SELECT id
                FROM careerops_v2.processing_jobs
                WHERE (
                    status IN ('pending', 'deferred', 'retryable_failure')
                    AND next_attempt_at <= now()
                ) OR (
                    status IN ('claimed', 'running')
                    AND lease_expires_at <= now()
                )
                ORDER BY
                    CASE
                        WHEN status IN ('claimed', 'running') THEN lease_expires_at
                        ELSE next_attempt_at
                    END,
                    id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE careerops_v2.processing_jobs AS job
            SET status = 'claimed',
                attempt_count = job.attempt_count + 1,
                next_attempt_at = NULL,
                lease_owner = %s,
                lease_token = %s,
                leased_at = now(),
                lease_expires_at = now() + make_interval(secs => %s),
                finished_at = NULL,
                error_category = NULL,
                result_artifact_uri = NULL,
                updated_at = now()
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING
                job.id,
                job.vacancy_id,
                job.binding_id,
                job.binding_version,
                job.input_fingerprint,
                job.input_manifest_uri,
                job.pipeline_version,
                job.policy_version,
                job.status,
                job.attempt_count,
                job.lease_owner,
                job.lease_token,
                job.leased_at,
                job.lease_expires_at
            """,
            (normalized_worker, lease_token, lease_seconds),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return ProcessingJobRecord(
            id=UUID(str(row[0])),
            vacancy_id=int(row[1]),
            binding_id=int(row[2]),
            binding_version=int(row[3]),
            input_fingerprint=str(row[4]),
            input_manifest_uri=str(row[5]),
            pipeline_version=str(row[6]),
            policy_version=str(row[7]),
            status=ProcessingJobStatus(str(row[8])),
            attempt_count=int(row[9]),
            lease_owner=str(row[10]) if row[10] is not None else None,
            lease_token=UUID(str(row[11])) if row[11] is not None else None,
            leased_at=row[12],
            lease_expires_at=row[13],
        )

    async def mark_running(self, job: ProcessingJobRecord) -> None:
        token = self._required_token(job)
        cursor = await self._conn.execute(
            """
            UPDATE careerops_v2.processing_jobs
            SET status = 'running', updated_at = now()
            WHERE id = %s
              AND lease_token = %s
              AND status = 'claimed'
              AND lease_expires_at > now()
            """,
            (job.id, token),
        )
        self._require_one(cursor.rowcount, job.id)

    async def renew_lease(
        self,
        job: ProcessingJobRecord,
        *,
        lease_seconds: int = 300,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        token = self._required_token(job)
        cursor = await self._conn.execute(
            """
            UPDATE careerops_v2.processing_jobs
            SET lease_expires_at = now() + make_interval(secs => %s),
                updated_at = now()
            WHERE id = %s
              AND lease_token = %s
              AND status IN ('claimed', 'running')
              AND lease_expires_at > now()
            """,
            (lease_seconds, job.id, token),
        )
        self._require_one(cursor.rowcount, job.id)

    async def succeed(self, job: ProcessingJobRecord, *, result_artifact_uri: str) -> None:
        artifact_uri = self._non_empty(result_artifact_uri, "result_artifact_uri")
        if not artifact_uri.startswith("s3://") or artifact_uri == "s3://":
            raise ValueError("processing success requires an s3:// result artifact URI")
        token = self._required_token(job)
        cursor = await self._conn.execute(
            """
            UPDATE careerops_v2.processing_jobs
            SET status = 'succeeded',
                next_attempt_at = NULL,
                finished_at = now(),
                error_category = NULL,
                result_artifact_uri = %s,
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
            (artifact_uri, job.id, token),
        )
        self._require_one(cursor.rowcount, job.id)

    async def defer(
        self,
        job: ProcessingJobRecord,
        *,
        error_category: str,
        next_attempt_at: datetime,
    ) -> None:
        await self._release_retryable(
            job,
            status=ProcessingJobStatus.DEFERRED,
            error_category=error_category,
            next_attempt_at=next_attempt_at,
        )

    async def retryable_failure(
        self,
        job: ProcessingJobRecord,
        *,
        error_category: str,
        next_attempt_at: datetime,
    ) -> None:
        await self._release_retryable(
            job,
            status=ProcessingJobStatus.RETRYABLE_FAILURE,
            error_category=error_category,
            next_attempt_at=next_attempt_at,
        )

    async def terminal_failure(
        self,
        job: ProcessingJobRecord,
        *,
        error_category: str,
    ) -> None:
        normalized_error = self._non_empty(error_category, "error_category")
        token = self._required_token(job)
        cursor = await self._conn.execute(
            """
            UPDATE careerops_v2.processing_jobs
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
            (normalized_error, job.id, token),
        )
        self._require_one(cursor.rowcount, job.id)

    async def cancel(self, job: ProcessingJobRecord, *, reason: str = "cancelled") -> None:
        normalized_reason = self._non_empty(reason, "cancel reason")
        token = self._required_token(job)
        cursor = await self._conn.execute(
            """
            UPDATE careerops_v2.processing_jobs
            SET status = 'cancelled',
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
            (normalized_reason, job.id, token),
        )
        self._require_one(cursor.rowcount, job.id)

    async def _release_retryable(
        self,
        job: ProcessingJobRecord,
        *,
        status: ProcessingJobStatus,
        error_category: str,
        next_attempt_at: datetime,
    ) -> None:
        if status not in {ProcessingJobStatus.DEFERRED, ProcessingJobStatus.RETRYABLE_FAILURE}:
            raise ValueError("retryable release requires deferred or retryable_failure")
        normalized_error = self._non_empty(error_category, "error_category")
        if next_attempt_at.tzinfo is None or next_attempt_at.utcoffset() is None:
            raise ValueError("next_attempt_at must be timezone-aware")
        token = self._required_token(job)
        cursor = await self._conn.execute(
            """
            UPDATE careerops_v2.processing_jobs
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
            (status.value, next_attempt_at, normalized_error, job.id, token),
        )
        self._require_one(cursor.rowcount, job.id)

    async def _cancel_competing_active(self, spec: ProcessingWorkSpec) -> None:
        """Cancel active work whose exact identity differs from the desired spec."""

        await self._conn.execute(
            """
            UPDATE careerops_v2.processing_jobs
            SET status = 'cancelled',
                next_attempt_at = NULL,
                finished_at = now(),
                error_category = 'superseded',
                result_artifact_uri = NULL,
                lease_owner = NULL,
                lease_token = NULL,
                leased_at = NULL,
                lease_expires_at = NULL,
                updated_at = now()
            WHERE vacancy_id = %s
              AND binding_id = %s
              AND status IN (
                  'pending', 'claimed', 'running', 'deferred', 'retryable_failure'
              )
              AND NOT (
                  binding_version = %s
                  AND input_fingerprint = %s
                  AND pipeline_version = %s
                  AND policy_version = %s
              )
            """,
            (
                spec.vacancy_id,
                spec.binding_id,
                spec.binding_version,
                spec.input_fingerprint,
                spec.pipeline_version,
                spec.policy_version,
            ),
        )

    async def _lock_pair(self, pair: ProcessingPairKey) -> None:
        await self._conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"processing-pair:{pair.vacancy_id}:{pair.binding_id}",),
        )

    @staticmethod
    def _required_token(job: ProcessingJobRecord) -> UUID:
        if job.lease_token is None:
            raise ProcessingJobLeaseLost(f"processing job {job.id} has no lease token")
        return job.lease_token

    @staticmethod
    def _require_one(rowcount: int, job_id: UUID) -> None:
        if rowcount != 1:
            raise ProcessingJobLeaseLost(f"processing job lease lost for {job_id}")

    @staticmethod
    def _non_empty(value: str, name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{name} must not be empty")
        return normalized
