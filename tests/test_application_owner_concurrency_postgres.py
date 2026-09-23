from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
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


async def _seed_one_candidate(dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as connection:
        source = await connection.execute(
            "INSERT INTO careerops_v2.sources (source_key) "
            "VALUES ('hh-concurrency') RETURNING id"
        )
        source_id = int((await source.fetchone())[0])
        account = await connection.execute(
            """
            INSERT INTO careerops_v2.accounts (source_id, account_key)
            VALUES (%s, 'primary')
            RETURNING id
            """,
            (source_id,),
        )
        account_id = int((await account.fetchone())[0])
        profile = await connection.execute(
            """
            INSERT INTO careerops_v2.profiles (source_id, account_id, profile_key)
            VALUES (%s, %s, 'profile')
            RETURNING id
            """,
            (source_id, account_id),
        )
        profile_id = int((await profile.fetchone())[0])
        resume = await connection.execute(
            """
            INSERT INTO careerops_v2.resumes (
                source_id, account_id, profile_id, source_resume_id,
                lifecycle, present_in_upstream
            )
            VALUES (%s, %s, %s, 'resume', 'active', true)
            RETURNING id
            """,
            (source_id, account_id, profile_id),
        )
        resume_id = int((await resume.fetchone())[0])
        binding = await connection.execute(
            """
            INSERT INTO careerops_v2.resume_bindings (
                account_id, resume_id, binding_key, binding_version,
                target_key, enabled, auto_apply
            )
            VALUES (%s, %s, 'binding', 1, 'default', true, true)
            RETURNING id
            """,
            (account_id, resume_id),
        )
        binding_id = int((await binding.fetchone())[0])
        vacancy = await connection.execute(
            """
            INSERT INTO careerops_v2.vacancies (
                source_id, source_vacancy_id, archived, closed_for_applicants
            )
            VALUES (%s, 'vacancy', false, false)
            RETURNING id
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
            )
            VALUES (
                %s, %s, %s, 'eligible', 100,
                ARRAY['ok'], 's3://ci/result.json', now()
            )
            """,
            (vacancy_id, binding_id, job_id),
        )
        await connection.execute(
            """
            INSERT INTO careerops_v2.application_candidates (
                id, vacancy_id, binding_id, processing_job_id, status, expires_at
            )
            VALUES (%s, %s, %s, %s, 'eligible', now() + interval '1 hour')
            """,
            (uuid4(), vacancy_id, binding_id, job_id),
        )


@pytest.mark.asyncio
async def test_two_workers_cannot_claim_same_account_vacancy(
    application_target: PostgresTestTarget,
) -> None:
    await _seed_one_candidate(application_target.dsn)
    barrier = asyncio.Event()

    async def claim(worker: str) -> object | None:
        async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
            await barrier.wait()
            lease = await uow.applications.claim_next(worker_id=worker, lease_seconds=120)
            await uow.commit()
            return lease

    first = asyncio.create_task(claim("worker-a"))
    second = asyncio.create_task(claim("worker-b"))
    barrier.set()
    results = await asyncio.gather(first, second)

    claimed = [item for item in results if item is not None]
    assert len(claimed) == 1

    async with await psycopg.AsyncConnection.connect(
        application_target.dsn, autocommit=True
    ) as connection:
        app_count = await connection.execute("SELECT count(*) FROM careerops_v2.applications")
        guard_count = await connection.execute(
            "SELECT count(*) FROM careerops_v2.application_guards"
        )
        assert (await app_count.fetchone())[0] == 1
        assert (await guard_count.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_recovered_uncertain_submit_blocks_new_account_work(
    application_target: PostgresTestTarget,
) -> None:
    await _seed_one_candidate(application_target.dsn)

    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        lease = await uow.applications.claim_next(worker_id="worker-a", lease_seconds=120)
        assert lease is not None
        assert await uow.applications.mark_submitting(
            lease,
            audit_uri="s3://ci/precheck.json",
        )
        await uow.commit()

    async with await psycopg.AsyncConnection.connect(
        application_target.dsn, autocommit=True
    ) as connection:
        await connection.execute(
            """
            UPDATE careerops_v2.applications
            SET leased_at = now() - interval '2 minutes',
                lease_expires_at = now() - interval '1 minute'
            WHERE id = %s
            """,
            (lease.application_id,),
        )

    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        assert await uow.applications.recover_expired_leases() == 1
        await uow.commit()

    async with PostgresApplicationUnitOfWork(application_target.dsn) as uow:
        assert await uow.applications.claim_next(
            worker_id="worker-b",
            lease_seconds=120,
        ) is None
        await uow.commit()
