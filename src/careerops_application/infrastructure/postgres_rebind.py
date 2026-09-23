from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg

from ..domain import ApplicationLease
from . import postgres_claims


async def claim_rebind_candidate(
    connection: psycopg.AsyncConnection[Any],
    *,
    worker_id: str,
    lease_seconds: int,
    limit_cooldown_seconds: int,
) -> ApplicationLease | None:
    """Rebind one guarded pre-submit application to its newer current Processing result."""

    application = await connection.execute(
        """
        SELECT app.id,
               app.account_id,
               acc.account_key,
               app.source_vacancy_id,
               app.attempt_count,
               app.candidate_id,
               app.processing_job_id
        FROM careerops_v2.applications app
        JOIN careerops_v2.application_guards guard
          ON guard.account_id = app.account_id
         AND guard.source_vacancy_id = app.source_vacancy_id
         AND guard.application_id = app.id
        JOIN careerops_v2.accounts acc ON acc.id = app.account_id
        WHERE app.submitted_at IS NULL
          AND app.confirmed_at IS NULL
          AND app.lease_token IS NULL
          AND (
              (
                  app.status = 'blocked'
                  AND app.reason_code = 'application.candidate_stale_before_submit'
              )
              OR app.status = 'safe_failure'
          )
        ORDER BY app.state_changed_at, app.id
        FOR UPDATE OF app SKIP LOCKED
        LIMIT 1
        """
    )
    app_row = await application.fetchone()
    if app_row is None:
        return None

    application_id = UUID(str(app_row[0]))
    account_id = int(app_row[1])
    account_key = str(app_row[2])
    source_vacancy_id = str(app_row[3])
    old_candidate_id = UUID(str(app_row[5]))
    old_processing_job_id = UUID(str(app_row[6]))

    await postgres_claims._lock_account(connection, account_id=account_id)
    if not await postgres_claims._account_gate_open(
        connection,
        account_id=account_id,
        exclude_application_id=application_id,
        limit_cooldown_seconds=limit_cooldown_seconds,
    ):
        raise RuntimeError("stale rebind account gate unexpectedly closed")

    await connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"{account_id}:{source_vacancy_id}",),
    )
    guard = await connection.execute(
        """
        SELECT application_id
        FROM careerops_v2.application_guards
        WHERE account_id = %s AND source_vacancy_id = %s
        FOR UPDATE
        """,
        (account_id, source_vacancy_id),
    )
    guard_row = await guard.fetchone()
    if guard_row is None or UUID(str(guard_row[0])) != application_id:
        raise RuntimeError(f"stale rebind guard diagnostic: {guard_row!r}")

    current = await connection.execute(
        f"""
        SELECT ac.id,
               ac.processing_job_id,
               r.id,
               r.source_resume_id
        FROM careerops_v2.accounts acc
        JOIN careerops_v2.vacancies v
          ON v.source_id = acc.source_id
         AND v.source_vacancy_id = %(source_vacancy_id)s
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
        WHERE acc.id = %(account_id)s
          AND r.account_id = %(account_id)s
          AND (
              ac.id <> %(old_candidate_id)s
              OR ac.processing_job_id <> %(old_processing_job_id)s
          )
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
                AND newer.status IN ({postgres_claims._ACTIVE_PROCESSING_STATUSES})
                AND newer.created_at > pj.created_at
          )
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.applications unresolved
              WHERE unresolved.account_id = %(account_id)s
                AND unresolved.id <> %(application_id)s
                AND unresolved.status IN ({postgres_claims._UNRESOLVED_SUBMIT_STATUSES})
          )
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.applications active
              WHERE active.account_id = %(account_id)s
                AND active.id <> %(application_id)s
                AND active.status IN ({postgres_claims._LIVE_PRE_SUBMIT_STATUSES})
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
        ORDER BY ac.updated_at DESC, ac.id
        FOR UPDATE OF ac SKIP LOCKED
        LIMIT 1
        """,
        {
            "account_id": account_id,
            "application_id": application_id,
            "source_vacancy_id": source_vacancy_id,
            "old_candidate_id": old_candidate_id,
            "old_processing_job_id": old_processing_job_id,
            "limit_cooldown_seconds": limit_cooldown_seconds,
        },
    )
    current_row = await current.fetchone()
    if current_row is None:
        diagnostic = await connection.execute(
            """
            SELECT ac.id, ac.processing_job_id, ac.status, ac.expires_at > now(),
                   pj.status, mr.processing_job_id, mr.decision,
                   rb.account_id, rb.binding_version, rb.enabled, rb.auto_apply,
                   r.id, r.lifecycle, r.present_in_upstream,
                   v.source_vacancy_id, v.archived, v.closed_for_applicants,
                   app.processing_job_id, app.status, app.reason_code
            FROM careerops_v2.applications app
            JOIN careerops_v2.application_candidates ac ON ac.id = app.candidate_id
            JOIN careerops_v2.processing_jobs pj ON pj.id = ac.processing_job_id
            LEFT JOIN careerops_v2.match_results mr
              ON mr.vacancy_id = ac.vacancy_id
             AND mr.binding_id = ac.binding_id
            JOIN careerops_v2.resume_bindings rb ON rb.id = ac.binding_id
            JOIN careerops_v2.resumes r ON r.id = rb.resume_id
            JOIN careerops_v2.vacancies v ON v.id = ac.vacancy_id
            WHERE app.id = %s
            """,
            (application_id,),
        )
        raise RuntimeError(f"stale rebind current diagnostic: {await diagnostic.fetchall()!r}")

    candidate_id = UUID(str(current_row[0]))
    processing_job_id = UUID(str(current_row[1]))
    resume_id = int(current_row[2])
    source_resume_id = str(current_row[3])
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
        raise RuntimeError(f"failed to rebind stale application: {application_id}")

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
