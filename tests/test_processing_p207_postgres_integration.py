from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
from alembic.config import Config
from support.postgres import PostgresTestTarget

from alembic import command
from careerops_processing.contracts import (
    P207_RESULT_SCHEMA_VERSION,
    MatchDecision,
    MatchDecisionBundle,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
    ScoreBounds,
)
from careerops_processing.infrastructure import (
    PostgresMatchPublicationStore,
    PostgresProcessingJobStore,
)
from careerops_processing.queue import (
    ProcessingJobLeaseLost,
    ProcessingPairKey,
    ProcessingWorkSpec,
)

pytestmark = pytest.mark.integration_postgres

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT_A = "a" * 64
FINGERPRINT_B = "b" * 64
ARTIFACT_SHA = "c" * 64


def _alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


@pytest.fixture
def processing_target(
    v2_postgres_test_target: PostgresTestTarget,
) -> PostgresTestTarget:
    command.upgrade(_alembic_config(), "head")
    return v2_postgres_test_target


@pytest_asyncio.fixture
async def processing_connection(
    processing_target: PostgresTestTarget,
) -> AsyncIterator[psycopg.AsyncConnection[object]]:
    connection = await psycopg.AsyncConnection.connect(
        processing_target.dsn,
        autocommit=True,
    )
    try:
        yield connection
    finally:
        await connection.close()


async def _seed_pair(connection: psycopg.AsyncConnection[object]) -> tuple[int, int]:
    source_cursor = await connection.execute(
        "INSERT INTO careerops_v2.sources (source_key) VALUES ('hh-p207') RETURNING id"
    )
    source_row = await source_cursor.fetchone()
    assert source_row is not None
    source_id = int(source_row[0])

    account_cursor = await connection.execute(
        """
        INSERT INTO careerops_v2.accounts (source_id, account_key)
        VALUES (%s, 'p207-account')
        RETURNING id
        """,
        (source_id,),
    )
    account_row = await account_cursor.fetchone()
    assert account_row is not None
    account_id = int(account_row[0])

    profile_cursor = await connection.execute(
        """
        INSERT INTO careerops_v2.profiles (source_id, account_id, profile_key)
        VALUES (%s, %s, 'p207-profile')
        RETURNING id
        """,
        (source_id, account_id),
    )
    profile_row = await profile_cursor.fetchone()
    assert profile_row is not None
    profile_id = int(profile_row[0])

    resume_cursor = await connection.execute(
        """
        INSERT INTO careerops_v2.resumes (
            source_id,
            account_id,
            profile_id,
            source_resume_id,
            lifecycle,
            present_in_upstream
        )
        VALUES (%s, %s, %s, 'p207-resume', 'active', true)
        RETURNING id
        """,
        (source_id, account_id, profile_id),
    )
    resume_row = await resume_cursor.fetchone()
    assert resume_row is not None
    resume_id = int(resume_row[0])

    binding_cursor = await connection.execute(
        """
        INSERT INTO careerops_v2.resume_bindings (
            account_id,
            resume_id,
            binding_key,
            binding_version,
            target_key,
            enabled
        )
        VALUES (%s, %s, 'p207-binding', 1, 'de', true)
        RETURNING id
        """,
        (account_id, resume_id),
    )
    binding_row = await binding_cursor.fetchone()
    assert binding_row is not None
    binding_id = int(binding_row[0])

    vacancy_cursor = await connection.execute(
        """
        INSERT INTO careerops_v2.vacancies (source_id, source_vacancy_id)
        VALUES (%s, 'p207-vacancy')
        RETURNING id
        """,
        (source_id,),
    )
    vacancy_row = await vacancy_cursor.fetchone()
    assert vacancy_row is not None
    return int(vacancy_row[0]), binding_id


def _spec(vacancy_id: int, binding_id: int, fingerprint: str) -> ProcessingWorkSpec:
    return ProcessingWorkSpec(
        vacancy_id=vacancy_id,
        binding_id=binding_id,
        binding_version=1,
        input_fingerprint=fingerprint,
        input_manifest_uri=f"s3://careerops-artifacts/manifests/{fingerprint}.json",
        pipeline_version="processing-v2-test",
        policy_version="policy-v1",
    )


def _decision(fingerprint: str, decision: MatchDecision) -> MatchDecisionBundle:
    score = Decimal("100") if decision is MatchDecision.APPLICATION_CANDIDATE else Decimal("0")
    reason = {
        MatchDecision.APPLICATION_CANDIDATE: "match.policy_satisfied",
        MatchDecision.REVIEW: "match.policy_uncertain",
        MatchDecision.SKIP: "match.policy_not_satisfied",
    }[decision]
    return MatchDecisionBundle(
        input_fingerprint=fingerprint,
        requirement_qualification_set_sha256="d" * 64,
        scoring_version="scoring-v1",
        calibration_version="gold-v1",
        policy_version="policy-v1",
        decision=decision,
        score=ScoreBounds(lower=score, upper=score),
        deterministic_score=score,
        reason_codes=(reason,),
    )


def _result_ref(fingerprint: str) -> ProcessingArtifactRef:
    return ProcessingArtifactRef(
        kind=ProcessingArtifactKind.P2_07_RESULT,
        schema_version=P207_RESULT_SCHEMA_VERSION,
        uri=f"s3://careerops-artifacts/p2-07/{fingerprint}.json",
        sha256=ARTIFACT_SHA,
        size_bytes=123,
    )


async def _running_job(
    connection: psycopg.AsyncConnection[object],
    *,
    vacancy_id: int,
    binding_id: int,
    fingerprint: str,
):
    jobs = PostgresProcessingJobStore(connection)
    await jobs.reconcile_current(_spec(vacancy_id, binding_id, fingerprint))
    job = await jobs.claim_next(worker_id=f"worker-{fingerprint[0]}", lease_seconds=300)
    assert job is not None
    await jobs.mark_running(job)
    return jobs, job


async def _current_rows(
    connection: psycopg.AsyncConnection[object],
    vacancy_id: int,
    binding_id: int,
) -> tuple[tuple[object, ...] | None, tuple[object, ...] | None]:
    match_cursor = await connection.execute(
        """
        SELECT processing_job_id, decision, deterministic_score, artifact_uri
        FROM careerops_v2.match_results
        WHERE vacancy_id = %s AND binding_id = %s
        """,
        (vacancy_id, binding_id),
    )
    candidate_cursor = await connection.execute(
        """
        SELECT processing_job_id, status, expires_at > now()
        FROM careerops_v2.application_candidates
        WHERE vacancy_id = %s AND binding_id = %s
        """,
        (vacancy_id, binding_id),
    )
    return await match_cursor.fetchone(), await candidate_cursor.fetchone()


@pytest.mark.asyncio
async def test_application_candidate_publishes_current_match_and_live_candidate(
    processing_connection: psycopg.AsyncConnection[object],
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    _, job = await _running_job(
        processing_connection,
        vacancy_id=vacancy_id,
        binding_id=binding_id,
        fingerprint=FINGERPRINT_A,
    )
    publisher = PostgresMatchPublicationStore(processing_connection)

    await publisher.publish_current(
        job,
        decision=_decision(FINGERPRINT_A, MatchDecision.APPLICATION_CANDIDATE),
        result_ref=_result_ref(FINGERPRINT_A),
        candidate_ttl_seconds=3600,
    )

    match_row, candidate_row = await _current_rows(
        processing_connection, vacancy_id, binding_id
    )
    assert match_row is not None
    assert str(match_row[0]) == str(job.id)
    assert match_row[1] == "eligible"
    assert Decimal(str(match_row[2])) == Decimal("100.0000")
    assert match_row[3] == _result_ref(FINGERPRINT_A).uri
    assert candidate_row is not None
    assert str(candidate_row[0]) == str(job.id)
    assert candidate_row[1:] == ("eligible", True)


@pytest.mark.asyncio
async def test_expired_lease_cannot_publish_current_output(
    processing_connection: psycopg.AsyncConnection[object],
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    _, job = await _running_job(
        processing_connection,
        vacancy_id=vacancy_id,
        binding_id=binding_id,
        fingerprint=FINGERPRINT_A,
    )
    await processing_connection.execute(
        """
        UPDATE careerops_v2.processing_jobs
        SET leased_at = now() - interval '2 minutes',
            lease_expires_at = now() - interval '1 minute'
        WHERE id = %s
        """,
        (job.id,),
    )

    publisher = PostgresMatchPublicationStore(processing_connection)
    with pytest.raises(ProcessingJobLeaseLost):
        await publisher.publish_current(
            job,
            decision=_decision(FINGERPRINT_A, MatchDecision.APPLICATION_CANDIDATE),
            result_ref=_result_ref(FINGERPRINT_A),
            candidate_ttl_seconds=3600,
        )

    assert await _current_rows(processing_connection, vacancy_id, binding_id) == (None, None)


@pytest.mark.asyncio
async def test_superseding_job_invalidates_older_current_output(
    processing_connection: psycopg.AsyncConnection[object],
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    jobs, job = await _running_job(
        processing_connection,
        vacancy_id=vacancy_id,
        binding_id=binding_id,
        fingerprint=FINGERPRINT_A,
    )
    publisher = PostgresMatchPublicationStore(processing_connection)
    await publisher.publish_current(
        job,
        decision=_decision(FINGERPRINT_A, MatchDecision.APPLICATION_CANDIDATE),
        result_ref=_result_ref(FINGERPRINT_A),
        candidate_ttl_seconds=3600,
    )

    await jobs.reconcile_current(_spec(vacancy_id, binding_id, FINGERPRINT_B))

    match_row, candidate_row = await _current_rows(
        processing_connection, vacancy_id, binding_id
    )
    assert match_row is None
    assert candidate_row is not None
    assert candidate_row[1] == "withdrawn"


@pytest.mark.asyncio
async def test_reconciliation_cancel_after_publish_invalidates_output(
    processing_connection: psycopg.AsyncConnection[object],
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    jobs, job = await _running_job(
        processing_connection,
        vacancy_id=vacancy_id,
        binding_id=binding_id,
        fingerprint=FINGERPRINT_A,
    )
    publisher = PostgresMatchPublicationStore(processing_connection)
    await publisher.publish_current(
        job,
        decision=_decision(FINGERPRINT_A, MatchDecision.APPLICATION_CANDIDATE),
        result_ref=_result_ref(FINGERPRINT_A),
        candidate_ttl_seconds=3600,
    )

    assert await jobs.withdraw_pair(ProcessingPairKey(vacancy_id, binding_id)) == 1

    match_row, candidate_row = await _current_rows(
        processing_connection, vacancy_id, binding_id
    )
    assert match_row is None
    assert candidate_row is not None
    assert candidate_row[1] == "withdrawn"
    with pytest.raises(ProcessingJobLeaseLost):
        await jobs.succeed(job, result_artifact_uri=_result_ref(FINGERPRINT_A).uri)


@pytest.mark.asyncio
async def test_explicit_withdraw_after_success_clears_current_result(
    processing_connection: psycopg.AsyncConnection[object],
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    jobs, job = await _running_job(
        processing_connection,
        vacancy_id=vacancy_id,
        binding_id=binding_id,
        fingerprint=FINGERPRINT_A,
    )
    publisher = PostgresMatchPublicationStore(processing_connection)
    result_ref = _result_ref(FINGERPRINT_A)
    await publisher.publish_current(
        job,
        decision=_decision(FINGERPRINT_A, MatchDecision.APPLICATION_CANDIDATE),
        result_ref=result_ref,
        candidate_ttl_seconds=3600,
    )
    await jobs.succeed(job, result_artifact_uri=result_ref.uri)

    assert await jobs.withdraw_pair(ProcessingPairKey(vacancy_id, binding_id)) == 0

    match_row, candidate_row = await _current_rows(
        processing_connection, vacancy_id, binding_id
    )
    assert match_row is None
    assert candidate_row is not None
    assert candidate_row[1] == "withdrawn"


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [MatchDecision.REVIEW, MatchDecision.SKIP])
async def test_non_candidate_decision_withdraws_existing_candidate(
    processing_connection: psycopg.AsyncConnection[object],
    decision: MatchDecision,
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    jobs, first_job = await _running_job(
        processing_connection,
        vacancy_id=vacancy_id,
        binding_id=binding_id,
        fingerprint=FINGERPRINT_A,
    )
    publisher = PostgresMatchPublicationStore(processing_connection)
    first_ref = _result_ref(FINGERPRINT_A)
    await publisher.publish_current(
        first_job,
        decision=_decision(FINGERPRINT_A, MatchDecision.APPLICATION_CANDIDATE),
        result_ref=first_ref,
        candidate_ttl_seconds=3600,
    )
    await jobs.succeed(first_job, result_artifact_uri=first_ref.uri)

    await jobs.reconcile_current(_spec(vacancy_id, binding_id, FINGERPRINT_B))
    second_job = await jobs.claim_next(worker_id="worker-b", lease_seconds=300)
    assert second_job is not None
    await jobs.mark_running(second_job)
    await publisher.publish_current(
        second_job,
        decision=_decision(FINGERPRINT_B, decision),
        result_ref=_result_ref(FINGERPRINT_B),
        candidate_ttl_seconds=None,
    )

    match_row, candidate_row = await _current_rows(
        processing_connection, vacancy_id, binding_id
    )
    assert match_row is not None
    assert match_row[1] == ("review" if decision is MatchDecision.REVIEW else "rejected")
    assert candidate_row is not None
    assert candidate_row[1] == "withdrawn"
