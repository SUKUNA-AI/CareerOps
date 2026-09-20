from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from alembic.config import Config
from support.postgres import PostgresTestTarget

from alembic import command
from careerops_application.infrastructure.postgres import PostgresApplicationUnitOfWork

pytestmark = pytest.mark.integration_postgres
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


@pytest.fixture
def application_target(v2_postgres_test_target: PostgresTestTarget) -> PostgresTestTarget:
    command.upgrade(_config(), "head")
    return v2_postgres_test_target


@pytest_asyncio.fixture
async def application_connection(
    application_target: PostgresTestTarget,
) -> AsyncIterator[psycopg.AsyncConnection[object]]:
    connection = await psycopg.AsyncConnection.connect(application_target.dsn, autocommit=True)
    try:
        yield connection
    finally:
        await connection.close()


async def _seed_candidate(connection: psycopg.AsyncConnection[object]) -> UUID:
    source = await connection.execute(
        "INSERT INTO careerops_v2.sources (source_key) VALUES ('hh-p208') RETURNING id"
    )
    source_id = int((await source.fetchone())[0])
    account = await connection.execute(
        "INSERT INTO careerops_v2.accounts (source_id, account_key) VALUES (%s, 'primary') RETURNING id",
        (source_id,),
    )
    account_id = int((await account.fetchone())[0])
    profile = await connection.execute(
        "INSERT INTO careerops_v2.profiles (source_id, account_id, profile_key) VALUES (%s, %s, 'profile') RETURNING id",
        (source_id, account_id),
    )
    profile_id = int((await profile.fetchone())[0])
    resume = await connection.execute(
        """
        INSERT INTO careerops_v2.resumes (
            source_id, account_id, profile_id, source_resume_id, lifecycle, present_in_upstream
        ) VALUES (%s, %s, %s, 'resume-hash', 'active', true) RETURNING id
        """,
        (source_id, account_id, profile_id),
    )
    resume_id = int((await resume.fetchone())[0])
    binding = await connection.execute(
        """
        INSERT INTO careerops_v2.resume_bindings (
            account_id, resume_id, binding_key, binding_version, target_key, enabled, auto_apply
        ) VALUES (%s, %s, 'binding', 1, 'de', true, true) RETURNING id
        """,
        (account_id, resume_id),
    )
    binding_id = int((await binding.fetchone())[0])
    vacancy = await connection.execute(
        """
        INSERT INTO careerops_v2.vacancies (
            source_id, source_vacancy_id, archived, closed_for_applicants
        ) VALUES (%s, 'vacancy-123', false, false) RETURNING id
        """,
        (source_id,),
    )
    vacancy_id = int((await vacancy.fetchone())[0])
    job_id = uuid4()
    await connection.execute(
        """
        INSERT INTO careerops_v2.processing_jobs (
            id, vacancy_id, binding_id, binding_version, input_fingerprint,
            input_manifest_uri, pipeline_version, policy_version,
            status, next_attempt_at, finished_at, result_artifact_uri
        ) VALUES (
            %s, %s, %s, 1, %s, 's3://ci/manifest.json', 'pipeline', 'policy',
            'succeeded', NULL, now(), 's3://ci/result.json'
        )
        """,
        (job_id, vacancy_id, binding_id, "a" * 64),
    )
    await connection.execute(
        """
        INSERT INTO careerops_v2.match_results (
            vacancy_id, binding_id, processing_job_id, decision,
            deterministic_score, reason_codes, artifact_uri, computed_at
        ) VALUES (%s, %s, %s, 'eligible', 95, ARRAY['ok'], 's3://ci/result.json', now())
        """,
        (vacancy_id, binding_id, job_id),
    )
    candidate_id = uuid4()
    await connection.execute(
        """
        INSERT INTO careerops_v2.application_candidates (
            id, vacancy_id, binding_id, processing_job_id, status, expires_at
        ) VALUES (%s, %s, %s, %s, 'eligible', now() + interval '1 hour')
        """,
        (candidate_id, vacancy_id, binding_id, job_id),
    )
    return candidate_id


async def _publish_newer_current_result(
    connection: psycopg.AsyncConnection[object], candidate_id: UUID
) -> UUID:
    current = await connection.execute(
        "SELECT vacancy_id, binding_id FROM careerops_v2.application_candidates WHERE id = %s",
        (candidate_id,),
    )
    row = await current.fetchone()
    assert row is not None
    vacancy_id, binding_id = int(row[0]), int(row[1])
    job_id = uuid4()
    await connection.execute(
        """
        INSERT INTO careerops_v2.processing_jobs (
            id, vacancy_id, binding_id, binding_version, input_fingerprint,
            input_manifest_uri, pipeline_version, policy_version,
            status, next_attempt_at, finished_at, result_artifact_uri
        ) VALUES (
            %s, %s, %s, 1, %s, 's3://ci/manifest-new.json', 'pipeline', 'policy',
            'succeeded', NULL, now(), 's3://ci/result-new.json'
        )
        """,
        (job_id, vacancy_id, binding_id, "b" * 64),
    )
    await connection.execute(
        """
        UPDATE careerops_v2.match_results
        SET processing_job_id = %s, deterministic_score = 96,
            artifact_uri = 's3://ci/result-new.json', computed_at = now(), updated_at = now()
        WHERE vacancy_id = %s AND binding_id = %s
        """,
        (job_id, vacancy_id, binding_id),
    )
    await connection.execute(
        """
        UPDATE careerops_v2.application_candidates
        SET processing_job_id = %s, status = 'eligible',
            expires_at = now() + interval '1 hour', updated_at = now()
        WHERE id = %s
        """,
        (job_id, candidate_id),
    )
    return job_id


@pytest.mark.asyncio
async def test_claim_creates_permanent_guard_atomically(
    application_target: PostgresTestTarget,
    application_connection: psycopg.AsyncConnection[object],
) -> None:
    candidate_id = await _seed_candidate(application_connection)
    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        lease = await uow.applications.claim_next(worker_id="worker-a", lease_seconds=120)
        assert lease is not None and lease.candidate_id == candidate_id
        await uow.commit()
    guard = await application_connection.execute(
        "SELECT application_id FROM careerops_v2.application_guards WHERE account_id = %s AND source_vacancy_id = %s",
        (lease.account_id, lease.source_vacancy_id),
    )
    assert UUID(str((await guard.fetchone())[0])) == lease.application_id
    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        assert await uow.applications.claim_next(worker_id="worker-b", lease_seconds=120) is None


@pytest.mark.asyncio
async def test_expired_submitting_requires_reconciliation_and_blocks_new_work(
    application_target: PostgresTestTarget,
    application_connection: psycopg.AsyncConnection[object],
) -> None:
    await _seed_candidate(application_connection)
    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        lease = await uow.applications.claim_next(worker_id="worker-a", lease_seconds=120)
        assert lease is not None
        assert await uow.applications.mark_submitting(lease, audit_uri="s3://ci/precheck.json")
        await uow.commit()
    await application_connection.execute(
        "UPDATE careerops_v2.applications SET leased_at=now()-interval '2 min', lease_expires_at=now()-interval '1 min' WHERE id=%s",
        (lease.application_id,),
    )
    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        assert await uow.applications.recover_expired_leases() == 1
        await uow.commit()
    row = await (
        await application_connection.execute(
            "SELECT status, next_attempt_at, next_reconcile_at FROM careerops_v2.applications WHERE id=%s",
            (lease.application_id,),
        )
    ).fetchone()
    assert row == ("reconciliation_required", None, row[2])
    assert row[2] is not None
    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        assert await uow.applications.claim_next(worker_id="worker-b", lease_seconds=120) is None


@pytest.mark.asyncio
async def test_stale_pre_submit_guard_rebinds_same_application_to_current_result(
    application_target: PostgresTestTarget,
    application_connection: psycopg.AsyncConnection[object],
) -> None:
    candidate_id = await _seed_candidate(application_connection)
    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        lease = await uow.applications.claim_next(worker_id="worker-a", lease_seconds=120)
        assert lease is not None
        await uow.commit()

    newer_job_id = await _publish_newer_current_result(application_connection, candidate_id)
    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        assert not await uow.applications.mark_submitting(lease, audit_uri="s3://ci/old.json")
        await uow.applications.mark_blocked(
            lease,
            reason_code="application.candidate_stale_before_submit",
            audit_uri="s3://ci/stale.json",
        )
        await uow.commit()

    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        rebound = await uow.applications.claim_next(worker_id="worker-b", lease_seconds=120)
        assert rebound is not None
        assert rebound.application_id == lease.application_id
        assert rebound.candidate_id == candidate_id
        assert rebound.processing_job_id == newer_job_id
        assert rebound.attempt_count == 2
        await uow.commit()


@pytest.mark.asyncio
async def test_expired_lease_cannot_commit_transition(
    application_target: PostgresTestTarget,
    application_connection: psycopg.AsyncConnection[object],
) -> None:
    await _seed_candidate(application_connection)
    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        lease = await uow.applications.claim_next(worker_id="worker-a", lease_seconds=120)
        assert lease is not None
        await uow.commit()
    await application_connection.execute(
        "UPDATE careerops_v2.applications SET leased_at=now()-interval '2 min', lease_expires_at=now()-interval '1 min' WHERE id=%s",
        (lease.application_id,),
    )
    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        with pytest.raises(RuntimeError, match="application lease lost"):
            await uow.applications.mark_safe_failure(
                lease,
                reason_code="application.test",
                audit_uri="s3://ci/test.json",
                retry_after_seconds=1,
            )


@pytest.mark.asyncio
async def test_withdrawn_candidate_cannot_cross_precheck_to_submit(
    application_target: PostgresTestTarget,
    application_connection: psycopg.AsyncConnection[object],
) -> None:
    candidate_id = await _seed_candidate(application_connection)
    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        lease = await uow.applications.claim_next(worker_id="worker-a", lease_seconds=120)
        assert lease is not None
        await uow.commit()
    await application_connection.execute(
        "UPDATE careerops_v2.application_candidates SET status='withdrawn', updated_at=now() WHERE id=%s",
        (candidate_id,),
    )
    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        assert not await uow.applications.mark_submitting(lease, audit_uri="s3://ci/precheck.json")
