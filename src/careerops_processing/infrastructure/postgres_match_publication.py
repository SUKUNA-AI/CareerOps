"""Fenced PostgreSQL publication of current P2-07 match state."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from psycopg import AsyncConnection

from careerops_processing.contracts import (
    P207_RESULT_SCHEMA_VERSION,
    MatchDecision,
    MatchDecisionBundle,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
)
from careerops_processing.queue import ProcessingJobLeaseLost, ProcessingJobRecord


class PostgresMatchPublicationStore:
    """Publishes only the current fenced match result; S3 remains historical truth."""

    def __init__(self, conn: AsyncConnection[Any]) -> None:
        if not conn.autocommit:
            raise ValueError("PostgresMatchPublicationStore requires an autocommit connection")
        self._conn = conn

    async def publish_current(
        self,
        job: ProcessingJobRecord,
        *,
        decision: MatchDecisionBundle,
        result_ref: ProcessingArtifactRef,
        candidate_ttl_seconds: int | None,
    ) -> None:
        if result_ref.kind is not ProcessingArtifactKind.P2_07_RESULT:
            raise ValueError("current publication requires a P2_07_RESULT artifact")
        if result_ref.schema_version != P207_RESULT_SCHEMA_VERSION:
            raise ValueError("current publication received unsupported P2-07 schema")
        if decision.input_fingerprint != job.input_fingerprint:
            raise ValueError("match decision input_fingerprint does not match job")
        if decision.policy_version != job.policy_version:
            raise ValueError("match decision policy_version does not match job")
        if decision.decision is MatchDecision.APPLICATION_CANDIDATE:
            if candidate_ttl_seconds is None or candidate_ttl_seconds <= 0:
                raise ValueError("APPLICATION_CANDIDATE requires calibrated candidate TTL")
        elif candidate_ttl_seconds is not None:
            raise ValueError("candidate TTL is only valid for APPLICATION_CANDIDATE")

        token = job.lease_token
        if token is None:
            raise ProcessingJobLeaseLost(f"processing job {job.id} has no lease token")

        db_decision = {
            MatchDecision.APPLICATION_CANDIDATE: "eligible",
            MatchDecision.REVIEW: "review",
            MatchDecision.SKIP: "rejected",
        }[decision.decision]

        async with self._conn.transaction():
            await self._conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"processing-pair:{job.vacancy_id}:{job.binding_id}",),
            )
            cursor = await self._conn.execute(
                """
                SELECT 1
                FROM careerops_v2.processing_jobs
                WHERE id = %s
                  AND vacancy_id = %s
                  AND binding_id = %s
                  AND binding_version = %s
                  AND input_fingerprint = %s
                  AND pipeline_version = %s
                  AND policy_version = %s
                  AND status = 'running'
                  AND lease_token = %s
                  AND lease_expires_at > now()
                FOR UPDATE
                """,
                (
                    job.id,
                    job.vacancy_id,
                    job.binding_id,
                    job.binding_version,
                    job.input_fingerprint,
                    job.pipeline_version,
                    job.policy_version,
                    token,
                ),
            )
            if await cursor.fetchone() is None:
                raise ProcessingJobLeaseLost(
                    f"processing job is stale before current publication: {job.id}"
                )

            await self._conn.execute(
                """
                INSERT INTO careerops_v2.match_results (
                    vacancy_id,
                    binding_id,
                    processing_job_id,
                    decision,
                    deterministic_score,
                    reason_codes,
                    artifact_uri,
                    computed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (vacancy_id, binding_id)
                DO UPDATE SET
                    processing_job_id = EXCLUDED.processing_job_id,
                    decision = EXCLUDED.decision,
                    deterministic_score = EXCLUDED.deterministic_score,
                    reason_codes = EXCLUDED.reason_codes,
                    artifact_uri = EXCLUDED.artifact_uri,
                    computed_at = EXCLUDED.computed_at,
                    updated_at = now()
                """,
                (
                    job.vacancy_id,
                    job.binding_id,
                    job.id,
                    db_decision,
                    decision.deterministic_score,
                    list(decision.reason_codes),
                    result_ref.uri,
                ),
            )

            if decision.decision is MatchDecision.APPLICATION_CANDIDATE:
                assert candidate_ttl_seconds is not None
                await self._conn.execute(
                    """
                    INSERT INTO careerops_v2.application_candidates (
                        id,
                        vacancy_id,
                        binding_id,
                        processing_job_id,
                        status,
                        expires_at
                    )
                    VALUES (
                        %s, %s, %s, %s, 'eligible',
                        now() + make_interval(secs => %s)
                    )
                    ON CONFLICT (vacancy_id, binding_id)
                    DO UPDATE SET
                        processing_job_id = EXCLUDED.processing_job_id,
                        status = 'eligible',
                        expires_at = EXCLUDED.expires_at,
                        updated_at = now()
                    """,
                    (
                        uuid4(),
                        job.vacancy_id,
                        job.binding_id,
                        job.id,
                        candidate_ttl_seconds,
                    ),
                )
                return

            await self._conn.execute(
                """
                UPDATE careerops_v2.application_candidates
                SET processing_job_id = %s,
                    status = 'withdrawn',
                    updated_at = now()
                WHERE vacancy_id = %s
                  AND binding_id = %s
                  AND status <> 'withdrawn'
                """,
                (job.id, job.vacancy_id, job.binding_id),
            )
