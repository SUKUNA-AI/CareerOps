from __future__ import annotations

from types import TracebackType
from typing import Any
from uuid import UUID

import psycopg

from ..domain import ApplicationLease, ApplicationStateView
from . import postgres_claims


class PostgresApplicationRepository:
    def __init__(
        self,
        connection: psycopg.AsyncConnection[Any],
        *,
        account_limit_cooldown_seconds: int = 900,
    ) -> None:
        self._connection = connection
        self._account_limit_cooldown_seconds = account_limit_cooldown_seconds

    async def recover_expired_leases(self) -> int:
        return await postgres_claims.recover_expired_leases(self._connection)

    async def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> ApplicationLease | None:
        retry = await postgres_claims.claim_retry(
            self._connection,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            limit_cooldown_seconds=self._account_limit_cooldown_seconds,
        )
        if retry is not None:
            return retry
        rebound = await postgres_claims.claim_rebind_candidate(
            self._connection,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            limit_cooldown_seconds=self._account_limit_cooldown_seconds,
        )
        if rebound is not None:
            return rebound
        return await postgres_claims.claim_new_candidate(
            self._connection,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            limit_cooldown_seconds=self._account_limit_cooldown_seconds,
        )

    async def claim_reconciliation(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> ApplicationLease | None:
        return await postgres_claims.claim_reconciliation(
            self._connection,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            limit_cooldown_seconds=self._account_limit_cooldown_seconds,
        )

    async def renew_lease(self, lease: ApplicationLease, *, lease_seconds: int) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        cursor = await self._connection.execute(
            """
            UPDATE careerops_v2.applications
            SET lease_expires_at = now() + make_interval(secs => %(lease_seconds)s),
                updated_at = now()
            WHERE id = %(application_id)s
              AND lease_token = %(lease_token)s
              AND lease_expires_at > now()
              AND status IN ('preparing', 'precheck', 'submitting', 'reconciliation_required')
            RETURNING id
            """,
            {
                "application_id": lease.application_id,
                "lease_token": lease.lease_token,
                "lease_seconds": lease_seconds,
            },
        )
        if await cursor.fetchone() is None:
            raise RuntimeError(f"application lease lost: {lease.application_id}")

    async def mark_submitting(self, lease: ApplicationLease, *, audit_uri: str) -> bool:
        cursor = await self._connection.execute(
            """
            UPDATE careerops_v2.applications app
            SET status = 'submitting',
                prechecked_at = now(),
                precheck_evidence_uri = %(audit_uri)s,
                audit_uri = %(audit_uri)s,
                reason_code = NULL,
                error_category = NULL,
                state_changed_at = now(),
                updated_at = now()
            WHERE app.id = %(application_id)s
              AND app.lease_token = %(lease_token)s
              AND app.lease_expires_at > now()
              AND EXISTS (
                  SELECT 1
                  FROM careerops_v2.application_candidates ac
                  JOIN careerops_v2.processing_jobs pj ON pj.id = ac.processing_job_id
                  JOIN careerops_v2.match_results mr
                    ON mr.vacancy_id = ac.vacancy_id
                   AND mr.binding_id = ac.binding_id
                   AND mr.processing_job_id = ac.processing_job_id
                  JOIN careerops_v2.resume_bindings rb ON rb.id = ac.binding_id
                  JOIN careerops_v2.resumes r
                    ON r.id = rb.resume_id AND r.account_id = rb.account_id
                  JOIN careerops_v2.vacancies v ON v.id = ac.vacancy_id
                  WHERE ac.id = app.candidate_id
                    AND pj.id = app.processing_job_id
                    AND ac.status = 'eligible'
                    AND ac.expires_at > now()
                    AND pj.status = 'succeeded'
                    AND mr.decision = 'eligible'
                    AND rb.enabled IS TRUE
                    AND rb.auto_apply IS TRUE
                    AND rb.binding_version = pj.binding_version
                    AND r.lifecycle = 'active'
                    AND r.present_in_upstream IS TRUE
                    AND v.archived IS DISTINCT FROM TRUE
                    AND v.closed_for_applicants IS DISTINCT FROM TRUE
                    AND NOT EXISTS (
                        SELECT 1
                        FROM careerops_v2.processing_jobs newer
                        WHERE newer.vacancy_id = ac.vacancy_id
                          AND newer.binding_id = ac.binding_id
                          AND newer.id <> pj.id
                          AND newer.status IN (
                              'pending', 'claimed', 'running', 'deferred', 'retryable_failure'
                          )
                          AND newer.created_at > pj.created_at
                    )
              )
            RETURNING app.id
            """,
            {
                "application_id": lease.application_id,
                "lease_token": lease.lease_token,
                "audit_uri": audit_uri,
            },
        )
        return await cursor.fetchone() is not None

    async def mark_submitted_unconfirmed(
        self,
        lease: ApplicationLease,
        *,
        audit_uri: str,
        reconcile_after_seconds: int,
    ) -> None:
        await self._transition(
            lease,
            """
            status = 'submitted_unconfirmed',
            submitted_at = COALESCE(submitted_at, now()),
            next_reconcile_at = now() + make_interval(secs => %(delay)s),
            next_attempt_at = NULL,
            audit_uri = %(audit_uri)s,
            reason_code = NULL,
            error_category = NULL,
            lease_owner = NULL,
            lease_token = NULL,
            leased_at = NULL,
            lease_expires_at = NULL,
            state_changed_at = now(),
            updated_at = now()
            """,
            {"audit_uri": audit_uri, "delay": reconcile_after_seconds},
        )

    async def mark_confirmed(self, lease: ApplicationLease, *, audit_uri: str) -> None:
        await self._transition(
            lease,
            """
            status = 'submitted_confirmed',
            submitted_at = COALESCE(submitted_at, now()),
            confirmed_at = now(),
            next_attempt_at = NULL,
            next_reconcile_at = NULL,
            upstream_evidence_uri = %(audit_uri)s,
            audit_uri = %(audit_uri)s,
            reason_code = NULL,
            error_category = NULL,
            lease_owner = NULL,
            lease_token = NULL,
            leased_at = NULL,
            lease_expires_at = NULL,
            state_changed_at = now(),
            updated_at = now()
            """,
            {"audit_uri": audit_uri},
        )

    async def mark_safe_failure(
        self,
        lease: ApplicationLease,
        *,
        reason_code: str,
        audit_uri: str,
        retry_after_seconds: int,
    ) -> None:
        await self._transition(
            lease,
            """
            status = 'safe_failure',
            next_attempt_at = now() + make_interval(secs => %(delay)s),
            next_reconcile_at = NULL,
            reason_code = %(reason_code)s,
            error_category = 'transport_safe_failure',
            audit_uri = %(audit_uri)s,
            lease_owner = NULL,
            lease_token = NULL,
            leased_at = NULL,
            lease_expires_at = NULL,
            state_changed_at = now(),
            updated_at = now()
            """,
            {
                "audit_uri": audit_uri,
                "delay": retry_after_seconds,
                "reason_code": reason_code,
            },
        )

    async def mark_uncertain(
        self,
        lease: ApplicationLease,
        *,
        reason_code: str,
        audit_uri: str,
        reconcile_after_seconds: int,
    ) -> None:
        await self._transition(
            lease,
            """
            status = 'uncertain',
            submitted_at = COALESCE(submitted_at, now()),
            next_attempt_at = NULL,
            next_reconcile_at = now() + make_interval(secs => %(delay)s),
            reason_code = %(reason_code)s,
            error_category = 'transport_uncertain',
            audit_uri = %(audit_uri)s,
            lease_owner = NULL,
            lease_token = NULL,
            leased_at = NULL,
            lease_expires_at = NULL,
            state_changed_at = now(),
            updated_at = now()
            """,
            {
                "audit_uri": audit_uri,
                "delay": reconcile_after_seconds,
                "reason_code": reason_code,
            },
        )

    async def mark_blocked(
        self,
        lease: ApplicationLease,
        *,
        reason_code: str,
        audit_uri: str,
    ) -> None:
        await self._transition(
            lease,
            """
            status = 'blocked',
            next_attempt_at = NULL,
            next_reconcile_at = NULL,
            reason_code = %(reason_code)s,
            error_category = 'blocked',
            audit_uri = %(audit_uri)s,
            lease_owner = NULL,
            lease_token = NULL,
            leased_at = NULL,
            lease_expires_at = NULL,
            state_changed_at = now(),
            updated_at = now()
            """,
            {"audit_uri": audit_uri, "reason_code": reason_code},
        )

    async def reschedule_reconciliation(
        self,
        lease: ApplicationLease,
        *,
        audit_uri: str,
        reconcile_after_seconds: int,
    ) -> None:
        await self._transition(
            lease,
            """
            status = 'reconciliation_required',
            next_attempt_at = NULL,
            next_reconcile_at = now() + make_interval(secs => %(delay)s),
            reason_code = 'application.reconciliation_pending',
            audit_uri = %(audit_uri)s,
            lease_owner = NULL,
            lease_token = NULL,
            leased_at = NULL,
            lease_expires_at = NULL,
            state_changed_at = now(),
            updated_at = now()
            """,
            {"audit_uri": audit_uri, "delay": reconcile_after_seconds},
        )

    async def get_state(self, application_id: UUID) -> ApplicationStateView | None:
        cursor = await self._connection.execute(
            """
            SELECT app.id, app.account_id, app.source_vacancy_id, r.source_resume_id,
                   app.status, app.attempt_count, app.reason_code, app.audit_uri
            FROM careerops_v2.applications app
            LEFT JOIN careerops_v2.resumes r ON r.id = app.resume_id
            WHERE app.id = %s
            """,
            (application_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return ApplicationStateView(
            application_id=UUID(str(row[0])),
            account_id=int(row[1]),
            source_vacancy_id=str(row[2]),
            source_resume_id=None if row[3] is None else str(row[3]),
            status=str(row[4]),
            attempt_count=int(row[5]),
            reason_code=None if row[6] is None else str(row[6]),
            audit_uri=None if row[7] is None else str(row[7]),
        )

    async def _transition(
        self,
        lease: ApplicationLease,
        assignments_sql: str,
        params: dict[str, object],
    ) -> None:
        query = f"""
            UPDATE careerops_v2.applications
            SET {assignments_sql}
            WHERE id = %(application_id)s
              AND lease_token = %(lease_token)s
              AND lease_expires_at > now()
            RETURNING id
        """
        values = {
            **params,
            "application_id": lease.application_id,
            "lease_token": lease.lease_token,
        }
        cursor = await self._connection.execute(query, values)
        if await cursor.fetchone() is None:
            raise RuntimeError(f"application lease lost: {lease.application_id}")


class PostgresApplicationUnitOfWork:
    def __init__(self, dsn: str, *, account_limit_cooldown_seconds: int = 900) -> None:
        self._dsn = dsn
        self._account_limit_cooldown_seconds = account_limit_cooldown_seconds
        self._connection: psycopg.AsyncConnection[Any] | None = None
        self.applications: PostgresApplicationRepository

    async def __aenter__(self) -> PostgresApplicationUnitOfWork:
        connection = await psycopg.AsyncConnection.connect(self._dsn, autocommit=False)
        self._connection = connection
        self.applications = PostgresApplicationRepository(
            connection,
            account_limit_cooldown_seconds=self._account_limit_cooldown_seconds,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        connection = self._require_connection()
        try:
            if exc_type is not None:
                await connection.rollback()
        finally:
            await connection.close()
            self._connection = None

    async def commit(self) -> None:
        await self._require_connection().commit()

    def _require_connection(self) -> psycopg.AsyncConnection[Any]:
        if self._connection is None:
            raise RuntimeError("PostgresApplicationUnitOfWork is not active")
        return self._connection
