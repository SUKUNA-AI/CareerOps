from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID, uuid4

import psycopg

from ..domain import ApplicationLease

_ACTIVE_PROCESSING_STATUSES = "'pending','claimed','running','deferred','retryable_failure'"
_ACTIVE_APPLICATION_STATUSES = "'preparing','precheck','submitting','reconciliation_required'"


async def recover_expired_leases(connection: psycopg.AsyncConnection[Any]) -> int:
    cursor = await connection.execute(
        """
        WITH recovered AS (
            UPDATE careerops_v2.applications
            SET status = CASE
                    WHEN status IN ('submitting', 'reconciliation_required')
                        THEN 'reconciliation_required'
                    ELSE 'safe_failure'
                END,
                next_attempt_at = CASE
                    WHEN status IN ('submitting', 'reconciliation_required') THEN NULL
                    ELSE now()
                END,
                next_reconcile_at = CASE
                    WHEN status IN ('submitting', 'reconciliation_required') THEN now()
                    ELSE NULL
                END,
                lease_owner = NULL,
                lease_token = NULL,
                leased_at = NULL,
                lease_expires_at = NULL,
                reason_code = CASE
                    WHEN status = 'submitting' THEN 'application.submit_outcome_unknown'
                    WHEN status = 'reconciliation_required'
                        THEN 'application.reconciliation_interrupted'
                    ELSE 'application.lease_expired_before_submit'
                END,
                error_category = CASE
                    WHEN status IN ('submitting', 'reconciliation_required')
                        THEN 'transport_uncertain'
                    ELSE 'worker_lease_expired'
                END,
                state_changed_at = now(),
                updated_at = now()
            WHERE lease_expires_at IS NOT NULL
              AND lease_expires_at <= now()
              AND status IN ('preparing', 'precheck', 'submitting', 'reconciliation_required')
            RETURNING 1
        )
        SELECT count(*) FROM recovered
        """
    )
    row = await cursor.fetchone()
    return int(row[0]) if row is not None else 0


async def _lock_account(
    connection: psycopg.AsyncConnection[Any],
    *,
    account_id: int,
) -> None:
    await connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"application-account:{account_id}",),
    )


async def _account_gate_open(
    connection: psycopg.AsyncConnection[Any],
    *,
    account_id: int,
    exclude_application_id: UUID | None,
    limit_cooldown_seconds: int,
) -> bool:
    cursor = await connection.execute(
        """
        SELECT
            NOT EXISTS (
                SELECT 1
                FROM careerops_v2.applications active
                WHERE active.account_id = %(account_id)s
                  AND active.id IS DISTINCT FROM %(exclude_application_id)s
                  AND active.status IN (
                      'preparing', 'precheck', 'submitting', 'reconciliation_required'
                  )
                  AND active.lease_expires_at > now()
            )
            AND NOT EXISTS (
                SELECT 1
                FROM careerops_v2.applications limited
                WHERE limited.account_id = %(account_id)s
                  AND limited.reason_code = 'application.hh_limit_exceeded'
                  AND limited.state_changed_at
                      > now() - make_interval(secs => %(limit_cooldown_seconds)s)
            )
        """,
        {
            "account_id": account_id,
            "exclude_application_id": exclude_application_id,
            "limit_cooldown_seconds": limit_cooldown_seconds,
        },
    )
    row = await cursor.fetchone()
    return bool(row[0]) if row is not None else False


async def claim_retry(
    connection: psycopg.AsyncConnection[Any],
    *,
    worker_id: str,
    lease_seconds: int,
    limit_cooldown_seconds: int,
) -> ApplicationLease | None:
    cursor = await connection.execute(
        f"""
        SELECT app.id, app.account_id, acc.account_key, app.source_vacancy_id,
               app.resume_id, r.source_resume_id, app.candidate_id,
               app.processing_job_id, app.attempt_count
        FROM careerops_v2.applications app
        JOIN careerops_v2.accounts acc ON acc.id = app.account_id
        JOIN careerops_v2.resumes r ON r.id = app.resume_id AND r.account_id = app.account_id
        JOIN careerops_v2.application_candidates ac ON ac.id = app.candidate_id
        JOIN careerops_v2.processing_jobs pj ON pj.id = app.processing_job_id
        JOIN careerops_v2.match_results mr
          ON mr.vacancy_id = ac.vacancy_id
         AND mr.binding_id = ac.binding_id
         AND mr.processing_job_id = ac.processing_job_id
        JOIN careerops_v2.resume_bindings rb ON rb.id = ac.binding_id
        JOIN careerops_v2.vacancies v ON v.id = ac.vacancy_id
        WHERE app.status = 'safe_failure'
          AND app.next_attempt_at <= now()
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
                AND newer.status IN ({_ACTIVE_PROCESSING_STATUSES})
                AND newer.created_at > pj.created_at
          )
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.applications active
              WHERE active.account_id = app.account_id
                AND active.id <> app.id
                AND active.status IN ({_ACTIVE_APPLICATION_STATUSES})
                AND active.lease_expires_at > now()
          )
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.applications limited
              WHERE limited.account_id = app.account_id
                AND limited.reason_code = 'application.hh_limit_exceeded'
                AND limited.state_changed_at
                    > now() - make_interval(secs => %(limit_cooldown_seconds)s)
          )
        ORDER BY app.next_attempt_at, app.created_at
        FOR UPDATE OF app SKIP LOCKED
        LIMIT 1
        """,
        {"limit_cooldown_seconds": limit_cooldown_seconds},
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    application_id = UUID(str(row[0]))
    account_id = int(row[1])
    await _lock_account(connection, account_id=account_id)
    if not await _account_gate_open(
        connection,
        account_id=account_id,
        exclude_application_id=application_id,
        limit_cooldown_seconds=limit_cooldown_seconds,
    ):
        return None

    lease_token = uuid4()
    updated = await connection.execute(
        """
        UPDATE careerops_v2.applications
        SET status = 'preparing',
            attempt_count = attempt_count + 1,
            next_attempt_at = NULL,
            error_category = NULL,
            reason_code = NULL,
            lease_owner = %s,
            lease_token = %s,
            leased_at = now(),
            lease_expires_at = now() + make_interval(secs => %s),
            state_changed_at = now(),
            updated_at = now()
        WHERE id = %s
          AND status = 'safe_failure'
          AND lease_token IS NULL
          AND next_attempt_at <= now()
        RETURNING attempt_count
        """,
        (worker_id, lease_token, lease_seconds, application_id),
    )
    attempt_row = await updated.fetchone()
    if attempt_row is None:
        raise RuntimeError("failed to claim safe retry application")
    return _lease_from_row(row, lease_token=lease_token, attempt_count=int(attempt_row[0]))


async def claim_rebind_candidate(
    connection: psycopg.AsyncConnection[Any],
    *,
    worker_id: str,
    lease_seconds: int,
    limit_cooldown_seconds: int,
) -> ApplicationLease | None:
    """Reuse an unsubmitted permanent guard when its previous candidate became stale."""

    cursor = await connection.execute(
        f"""
        SELECT app.id, app.account_id, acc.account_key, app.source_vacancy_id,
               app.attempt_count, ac.id, ac.processing_job_id,
               r.id, r.source_resume_id
        FROM careerops_v2.applications app
        JOIN careerops_v2.application_guards guard
          ON guard.account_id = app.account_id
         AND guard.source_vacancy_id = app.source_vacancy_id
         AND guard.application_id = app.id
        JOIN careerops_v2.accounts acc ON acc.id = app.account_id
        JOIN careerops_v2.application_candidates old_ac ON old_ac.id = app.candidate_id
        JOIN careerops_v2.processing_jobs old_pj ON old_pj.id = app.processing_job_id
        JOIN careerops_v2.vacancies v
          ON v.source_id = acc.source_id
         AND v.source_vacancy_id = app.source_vacancy_id
        JOIN careerops_v2.application_candidates ac ON ac.vacancy_id = v.id
        JOIN careerops_v2.processing_jobs pj ON pj.id = ac.processing_job_id
        JOIN careerops_v2.match_results mr
          ON mr.vacancy_id = ac.vacancy_id
         AND mr.binding_id = ac.binding_id
         AND mr.processing_job_id = ac.processing_job_id
        JOIN careerops_v2.resume_bindings rb ON rb.id = ac.binding_id
        JOIN careerops_v2.resumes r
          ON r.id = rb.resume_id
         AND r.account_id = rb.account_id
         AND r.account_id = app.account_id
        WHERE app.submitted_at IS NULL
          AND app.confirmed_at IS NULL
          AND app.lease_token IS NULL
          AND (
              (
                  app.status = 'blocked'
                  AND app.reason_code = 'application.candidate_stale_before_submit'
              )
              OR (
                  app.status = 'safe_failure'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM careerops_v2.application_candidates old_current
                      JOIN careerops_v2.processing_jobs old_job
                        ON old_job.id = old_current.processing_job_id
                      JOIN careerops_v2.match_results old_mr
                        ON old_mr.vacancy_id = old_current.vacancy_id
                       AND old_mr.binding_id = old_current.binding_id
                       AND old_mr.processing_job_id = old_current.processing_job_id
                      JOIN careerops_v2.resume_bindings old_rb
                        ON old_rb.id = old_current.binding_id
                      JOIN careerops_v2.resumes old_r
                        ON old_r.id = old_rb.resume_id
                       AND old_r.account_id = old_rb.account_id
                      JOIN careerops_v2.vacancies old_v
                        ON old_v.id = old_current.vacancy_id
                      WHERE old_current.id = app.candidate_id
                        AND old_job.id = app.processing_job_id
                        AND old_current.status = 'eligible'
                        AND old_current.expires_at > now()
                        AND old_job.status = 'succeeded'
                        AND old_mr.decision = 'eligible'
                        AND old_rb.enabled IS TRUE
                        AND old_rb.auto_apply IS TRUE
                        AND old_rb.binding_version = old_job.binding_version
                        AND old_r.lifecycle = 'active'
                        AND old_r.present_in_upstream IS TRUE
                        AND old_v.archived IS DISTINCT FROM TRUE
                        AND old_v.closed_for_applicants IS DISTINCT FROM TRUE
                        AND NOT EXISTS (
                            SELECT 1
                            FROM careerops_v2.processing_jobs newer_old
                            WHERE newer_old.vacancy_id = old_current.vacancy_id
                              AND newer_old.binding_id = old_current.binding_id
                              AND newer_old.id <> old_job.id
                              AND newer_old.status IN ({_ACTIVE_PROCESSING_STATUSES})
                              AND newer_old.created_at > old_job.created_at
                        )
                  )
              )
          )
          AND ac.id <> app.candidate_id
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
                AND newer.status IN ({_ACTIVE_PROCESSING_STATUSES})
                AND newer.created_at > pj.created_at
          )
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.applications active
              WHERE active.account_id = app.account_id
                AND active.id <> app.id
                AND active.status IN ({_ACTIVE_APPLICATION_STATUSES})
                AND active.lease_expires_at > now()
          )
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.applications limited
              WHERE limited.account_id = app.account_id
                AND limited.reason_code = 'application.hh_limit_exceeded'
                AND limited.state_changed_at
                    > now() - make_interval(secs => %(limit_cooldown_seconds)s)
          )
        ORDER BY app.state_changed_at, ac.created_at, ac.id
        FOR UPDATE OF app, ac SKIP LOCKED
        LIMIT 1
        """,
        {"limit_cooldown_seconds": limit_cooldown_seconds},
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    application_id = UUID(str(row[0]))
    account_id = int(row[1])
    account_key = str(row[2])
    source_vacancy_id = str(row[3])
    candidate_id = UUID(str(row[5]))
    processing_job_id = UUID(str(row[6]))
    resume_id = int(row[7])
    source_resume_id = str(row[8])

    await _lock_account(connection, account_id=account_id)
    if not await _account_gate_open(
        connection,
        account_id=account_id,
        exclude_application_id=application_id,
        limit_cooldown_seconds=limit_cooldown_seconds,
    ):
        return None

    lease_token = uuid4()
    updated = await connection.execute(
        """
        UPDATE careerops_v2.applications
        SET resume_id = %(resume_id)s,
            candidate_id = %(candidate_id)s,
            processing_job_id = %(processing_job_id)s,
            status = 'preparing',
            attempt_count = attempt_count + 1,
            next_attempt_at = NULL,
            next_reconcile_at = NULL,
            prechecked_at = NULL,
            precheck_evidence_uri = NULL,
            reason_code = NULL,
            error_category = NULL,
            lease_owner = %(worker_id)s,
            lease_token = %(lease_token)s,
            leased_at = now(),
            lease_expires_at = now() + make_interval(secs => %(lease_seconds)s),
            state_changed_at = now(),
            updated_at = now()
        WHERE id = %(application_id)s
          AND submitted_at IS NULL
          AND confirmed_at IS NULL
          AND lease_token IS NULL
          AND status IN ('blocked', 'safe_failure')
        RETURNING attempt_count
        """,
        {
            "application_id": application_id,
            "resume_id": resume_id,
            "candidate_id": candidate_id,
            "processing_job_id": processing_job_id,
            "worker_id": worker_id,
            "lease_token": lease_token,
            "lease_seconds": lease_seconds,
        },
    )
    attempt_row = await updated.fetchone()
    if attempt_row is None:
        raise RuntimeError("failed to rebind stale application candidate")

    return ApplicationLease(
        application_id=application_id,
        account_id=account_id,
        account_key=account_key,
        source_vacancy_id=source_vacancy_id,
        resume_id=resume_id,
        source_resume_id=source_resume_id,
        candidate_id=candidate_id,
        processing_job_id=processing_job_id,
        lease_token=lease_token,
        attempt_count=int(attempt_row[0]),
    )


async def claim_new_candidate(
    connection: psycopg.AsyncConnection[Any],
    *,
    worker_id: str,
    lease_seconds: int,
    limit_cooldown_seconds: int,
) -> ApplicationLease | None:
    cursor = await connection.execute(
        f"""
        SELECT ac.id, ac.processing_job_id, acc.id, acc.account_key,
               r.id, r.source_resume_id, v.source_vacancy_id
        FROM careerops_v2.application_candidates ac
        JOIN careerops_v2.processing_jobs pj ON pj.id = ac.processing_job_id
        JOIN careerops_v2.match_results mr
          ON mr.vacancy_id = ac.vacancy_id
         AND mr.binding_id = ac.binding_id
         AND mr.processing_job_id = ac.processing_job_id
        JOIN careerops_v2.resume_bindings rb ON rb.id = ac.binding_id
        JOIN careerops_v2.resumes r ON r.id = rb.resume_id AND r.account_id = rb.account_id
        JOIN careerops_v2.accounts acc ON acc.id = rb.account_id
        JOIN careerops_v2.vacancies v ON v.id = ac.vacancy_id
        WHERE ac.status = 'eligible'
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
              SELECT 1 FROM careerops_v2.application_guards g
              WHERE g.account_id = acc.id
                AND g.source_vacancy_id = v.source_vacancy_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.processing_jobs newer
              WHERE newer.vacancy_id = ac.vacancy_id
                AND newer.binding_id = ac.binding_id
                AND newer.id <> pj.id
                AND newer.status IN ({_ACTIVE_PROCESSING_STATUSES})
                AND newer.created_at > pj.created_at
          )
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.applications active
              WHERE active.account_id = acc.id
                AND active.status IN ({_ACTIVE_APPLICATION_STATUSES})
                AND active.lease_expires_at > now()
          )
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.applications limited
              WHERE limited.account_id = acc.id
                AND limited.reason_code = 'application.hh_limit_exceeded'
                AND limited.state_changed_at
                    > now() - make_interval(secs => %(limit_cooldown_seconds)s)
          )
        ORDER BY ac.created_at, ac.id
        FOR UPDATE OF ac SKIP LOCKED
        LIMIT 1
        """,
        {"limit_cooldown_seconds": limit_cooldown_seconds},
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    candidate_id = UUID(str(row[0]))
    processing_job_id = UUID(str(row[1]))
    account_id = int(row[2])
    account_key = str(row[3])
    resume_id = int(row[4])
    source_resume_id = str(row[5])
    source_vacancy_id = str(row[6])

    await _lock_account(connection, account_id=account_id)
    if not await _account_gate_open(
        connection,
        account_id=account_id,
        exclude_application_id=None,
        limit_cooldown_seconds=limit_cooldown_seconds,
    ):
        return None

    lock_key = f"{account_id}:{source_vacancy_id}"
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key,))
    guard = await connection.execute(
        """
        SELECT 1 FROM careerops_v2.application_guards
        WHERE account_id = %s AND source_vacancy_id = %s
        """,
        (account_id, source_vacancy_id),
    )
    if await guard.fetchone() is not None:
        return None

    application_id = uuid4()
    lease_token = uuid4()
    idempotency_key = hashlib.sha256(
        f"hh:account-vacancy-v1:{account_id}:{source_vacancy_id}".encode()
    ).hexdigest()
    await connection.execute(
        """
        INSERT INTO careerops_v2.applications (
            id, account_id, source_vacancy_id, idempotency_key,
            resume_id, candidate_id, processing_job_id,
            status, attempt_count, lease_owner, lease_token,
            leased_at, lease_expires_at, state_changed_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            'preparing', 1, %s, %s, now(),
            now() + make_interval(secs => %s), now()
        )
        """,
        (
            application_id,
            account_id,
            source_vacancy_id,
            idempotency_key,
            resume_id,
            candidate_id,
            processing_job_id,
            worker_id,
            lease_token,
            lease_seconds,
        ),
    )
    await connection.execute(
        """
        INSERT INTO careerops_v2.application_guards (
            account_id, source_vacancy_id, application_id, scope_policy
        )
        VALUES (%s, %s, %s, 'account_vacancy_v1')
        """,
        (account_id, source_vacancy_id, application_id),
    )
    return ApplicationLease(
        application_id=application_id,
        account_id=account_id,
        account_key=account_key,
        source_vacancy_id=source_vacancy_id,
        resume_id=resume_id,
        source_resume_id=source_resume_id,
        candidate_id=candidate_id,
        processing_job_id=processing_job_id,
        lease_token=lease_token,
        attempt_count=1,
    )


async def claim_reconciliation(
    connection: psycopg.AsyncConnection[Any],
    *,
    worker_id: str,
    lease_seconds: int,
    limit_cooldown_seconds: int,
) -> ApplicationLease | None:
    cursor = await connection.execute(
        f"""
        SELECT app.id, app.account_id, acc.account_key, app.source_vacancy_id,
               app.resume_id, r.source_resume_id, app.candidate_id,
               app.processing_job_id, app.attempt_count
        FROM careerops_v2.applications app
        JOIN careerops_v2.accounts acc ON acc.id = app.account_id
        JOIN careerops_v2.resumes r ON r.id = app.resume_id AND r.account_id = app.account_id
        WHERE app.status IN ('submitted_unconfirmed', 'uncertain', 'reconciliation_required')
          AND app.next_reconcile_at <= now()
          AND app.lease_token IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.applications active
              WHERE active.account_id = app.account_id
                AND active.id <> app.id
                AND active.status IN ({_ACTIVE_APPLICATION_STATUSES})
                AND active.lease_expires_at > now()
          )
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.applications limited
              WHERE limited.account_id = app.account_id
                AND limited.reason_code = 'application.hh_limit_exceeded'
                AND limited.state_changed_at
                    > now() - make_interval(secs => %(limit_cooldown_seconds)s)
          )
        ORDER BY app.next_reconcile_at, app.created_at
        FOR UPDATE OF app SKIP LOCKED
        LIMIT 1
        """,
        {"limit_cooldown_seconds": limit_cooldown_seconds},
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    application_id = UUID(str(row[0]))
    account_id = int(row[1])
    await _lock_account(connection, account_id=account_id)
    if not await _account_gate_open(
        connection,
        account_id=account_id,
        exclude_application_id=application_id,
        limit_cooldown_seconds=limit_cooldown_seconds,
    ):
        return None

    lease_token = uuid4()
    updated = await connection.execute(
        """
        UPDATE careerops_v2.applications
        SET status = 'reconciliation_required',
            lease_owner = %s,
            lease_token = %s,
            leased_at = now(),
            lease_expires_at = now() + make_interval(secs => %s),
            reason_code = 'application.reconciliation_pending',
            state_changed_at = now(),
            updated_at = now()
        WHERE id = %s
          AND status IN ('submitted_unconfirmed', 'uncertain', 'reconciliation_required')
          AND lease_token IS NULL
          AND next_reconcile_at <= now()
        RETURNING id
        """,
        (worker_id, lease_token, lease_seconds, application_id),
    )
    if await updated.fetchone() is None:
        raise RuntimeError("failed to claim reconciliation application")
    return _lease_from_row(row, lease_token=lease_token, attempt_count=int(row[8]))


def _lease_from_row(
    row: Any,
    *,
    lease_token: UUID,
    attempt_count: int,
) -> ApplicationLease:
    return ApplicationLease(
        application_id=UUID(str(row[0])),
        account_id=int(row[1]),
        account_key=str(row[2]),
        source_vacancy_id=str(row[3]),
        resume_id=int(row[4]),
        source_resume_id=str(row[5]),
        candidate_id=UUID(str(row[6])),
        processing_job_id=UUID(str(row[7])),
        lease_token=lease_token,
        attempt_count=attempt_count,
    )
