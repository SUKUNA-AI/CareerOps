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

    cursor = await connection.execute(
        f"""
        SELECT app.id,
               app.account_id,
               acc.account_key,
               app.source_vacancy_id,
               app.attempt_count,
               current_ac.id,
               current_ac.processing_job_id,
               current_resume.id,
               current_resume.source_resume_id
        FROM careerops_v2.applications app
        JOIN careerops_v2.application_guards guard
          ON guard.account_id = app.account_id
         AND guard.source_vacancy_id = app.source_vacancy_id
         AND guard.application_id = app.id
        JOIN careerops_v2.accounts acc
          ON acc.id = app.account_id
        JOIN careerops_v2.vacancies vacancy
          ON vacancy.source_id = acc.source_id
         AND vacancy.source_vacancy_id = app.source_vacancy_id
        JOIN careerops_v2.application_candidates current_ac
          ON current_ac.vacancy_id = vacancy.id
        JOIN careerops_v2.processing_jobs current_job
          ON current_job.id = current_ac.processing_job_id
        JOIN careerops_v2.match_results current_match
          ON current_match.vacancy_id = current_ac.vacancy_id
         AND current_match.binding_id = current_ac.binding_id
         AND current_match.processing_job_id = current_ac.processing_job_id
        JOIN careerops_v2.resume_bindings current_binding
          ON current_binding.id = current_ac.binding_id
        JOIN careerops_v2.resumes current_resume
          ON current_resume.id = current_binding.resume_id
         AND current_resume.account_id = current_binding.account_id
         AND current_resume.account_id = app.account_id
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
          AND (
              current_ac.id <> app.candidate_id
              OR current_ac.processing_job_id <> app.processing_job_id
          )
          AND current_ac.status = 'eligible'
          AND current_ac.expires_at > now()
          AND current_job.status = 'succeeded'
          AND current_match.decision = 'eligible'
          AND current_binding.enabled IS TRUE
          AND current_binding.auto_apply IS TRUE
          AND current_binding.binding_version = current_job.binding_version
          AND current_resume.lifecycle = 'active'
          AND current_resume.present_in_upstream IS TRUE
          AND vacancy.archived IS DISTINCT FROM TRUE
          AND vacancy.closed_for_applicants IS DISTINCT FROM TRUE
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.processing_jobs newer
              WHERE newer.vacancy_id = current_ac.vacancy_id
                AND newer.binding_id = current_ac.binding_id
                AND newer.id <> current_job.id
                AND newer.status IN ({postgres_claims._ACTIVE_PROCESSING_STATUSES})
                AND newer.created_at > current_job.created_at
          )
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.applications unresolved
              WHERE unresolved.account_id = app.account_id
                AND unresolved.id <> app.id
                AND unresolved.status IN ({postgres_claims._UNRESOLVED_SUBMIT_STATUSES})
          )
          AND NOT EXISTS (
              SELECT 1
              FROM careerops_v2.applications active
              WHERE active.account_id = app.account_id
                AND active.id <> app.id
                AND active.status IN ({postgres_claims._LIVE_PRE_SUBMIT_STATUSES})
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
        ORDER BY app.state_changed_at, current_ac.updated_at DESC, current_ac.id
        FOR UPDATE OF app, current_ac SKIP LOCKED
        LIMIT 1
        """,
        {"limit_cooldown_seconds": limit_cooldown_seconds},
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    application_id = UUID(str(row[0]))
    account_id = int(row[1])
    await postgres_claims._lock_account(connection, account_id=account_id)
    if not await postgres_claims._account_gate_open(
        connection,
        account_id=account_id,
        exclude_application_id=application_id,
        limit_cooldown_seconds=limit_cooldown_seconds,
    ):
        return None

    source_vacancy_id = str(row[3])
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
        return None

    lease_token = uuid4()
    candidate_id = UUID(str(row[5]))
    processing_job_id = UUID(str(row[6]))
    resume_id = int(row[7])
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
        account_key=str(row[2]),
        source_vacancy_id=source_vacancy_id,
        resume_id=resume_id,
        source_resume_id=str(row[8]),
        candidate_id=candidate_id,
        processing_job_id=processing_job_id,
        lease_token=lease_token,
        attempt_count=int(attempt_row[0]),
    )
