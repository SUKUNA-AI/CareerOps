from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
import pytest_asyncio
from alembic import command

from careerops_processing.infrastructure import PostgresProcessingJobStore
from careerops_processing.queue import (
    ProcessingJobLeaseLost,
    ProcessingPairKey,
    ProcessingWorkSpec,
)
from careerops_storage.alembic_cutover import (
    TEST_POSTGRES_DSN_ENV,
    DisposablePostgresTarget,
    assert_disposable_state_is_empty,
    build_alembic_config,
    reset_disposable_state,
    validate_disposable_postgres_dsn,
)

pytestmark = pytest.mark.integration_postgres


@pytest.fixture
def processing_target() -> Iterator[DisposablePostgresTarget]:
    dsn = os.getenv(TEST_POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{TEST_POSTGRES_DSN_ENV} is not configured")

    target = validate_disposable_postgres_dsn(dsn)
    reset_disposable_state(target)
    assert_disposable_state_is_empty(target)
    command.upgrade(build_alembic_config(target), "head")
    try:
        yield target
    finally:
        reset_disposable_state(target)


@pytest_asyncio.fixture
async def processing_connection(
    processing_target: DisposablePostgresTarget,
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
    cursor = await connection.execute(
        "INSERT INTO careerops_v2.sources (source_key) VALUES ('hh') RETURNING id"
    )
    source_row = await cursor.fetchone()
    assert source_row is not None
    source_id = int(source_row[0])

    cursor = await connection.execute(
        """
        INSERT INTO careerops_v2.accounts (source_id, account_key)
        VALUES (%s, 'processing-test')
        RETURNING id
        """,
        (source_id,),
    )
    account_row = await cursor.fetchone()
    assert account_row is not None
    account_id = int(account_row[0])

    cursor = await connection.execute(
        """
        INSERT INTO careerops_v2.profiles (source_id, account_id, profile_key)
        VALUES (%s, %s, 'processing-test-profile')
        RETURNING id
        """,
        (source_id, account_id),
    )
    profile_row = await cursor.fetchone()
    assert profile_row is not None
    profile_id = int(profile_row[0])

    cursor = await connection.execute(
        """
        INSERT INTO careerops_v2.resumes (
            source_id,
            account_id,
            profile_id,
            source_resume_id,
            lifecycle,
            present_in_upstream
        )
        VALUES (%s, %s, %s, 'resume-test', 'active', true)
        RETURNING id
        """,
        (source_id, account_id, profile_id),
    )
    resume_row = await cursor.fetchone()
    assert resume_row is not None
    resume_id = int(resume_row[0])

    cursor = await connection.execute(
        """
        INSERT INTO careerops_v2.resume_bindings (
            account_id,
            resume_id,
            binding_key,
            binding_version,
            target_key,
            enabled
        )
        VALUES (%s, %s, 'binding-test', 1, 'target-test', true)
        RETURNING id
        """,
        (account_id, resume_id),
    )
    binding_row = await cursor.fetchone()
    assert binding_row is not None
    binding_id = int(binding_row[0])

    cursor = await connection.execute(
        """
        INSERT INTO careerops_v2.vacancies (source_id, source_vacancy_id)
        VALUES (%s, 'vacancy-test')
        RETURNING id
        """,
        (source_id,),
    )
    vacancy_row = await cursor.fetchone()
    assert vacancy_row is not None
    vacancy_id = int(vacancy_row[0])
    return vacancy_id, binding_id


def _spec(vacancy_id: int, binding_id: int, fingerprint_char: str) -> ProcessingWorkSpec:
    return ProcessingWorkSpec(
        vacancy_id=vacancy_id,
        binding_id=binding_id,
        binding_version=1,
        input_fingerprint=fingerprint_char * 64,
        input_manifest_uri=(
            f"s3://careerops-artifacts/processing/manifests/{fingerprint_char * 64}.json"
        ),
        pipeline_version="processing-v2-test",
        policy_version="policy-v1",
    )


@pytest.mark.asyncio
async def test_reconcile_is_idempotent_and_supersedes_active_work(
    processing_connection: psycopg.AsyncConnection[object],
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    store = PostgresProcessingJobStore(processing_connection)

    first = _spec(vacancy_id, binding_id, "a")
    first_id = await store.reconcile_current(first)
    assert await store.reconcile_current(first) == first_id

    second = _spec(vacancy_id, binding_id, "b")
    second_id = await store.reconcile_current(second)
    assert second_id != first_id

    cursor = await processing_connection.execute(
        """
        SELECT id, status, error_category
        FROM careerops_v2.processing_jobs
        WHERE vacancy_id = %s AND binding_id = %s
        ORDER BY created_at, id
        """,
        (vacancy_id, binding_id),
    )
    rows = await cursor.fetchall()
    by_id = {str(row[0]): (str(row[1]), row[2]) for row in rows}
    assert by_id[str(first_id)] == ("cancelled", "superseded")
    assert by_id[str(second_id)] == ("pending", None)


@pytest.mark.asyncio
async def test_superseded_exact_work_can_become_current_again(
    processing_connection: psycopg.AsyncConnection[object],
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    store = PostgresProcessingJobStore(processing_connection)

    first = _spec(vacancy_id, binding_id, "a")
    second = _spec(vacancy_id, binding_id, "b")
    first_id = await store.reconcile_current(first)
    second_id = await store.reconcile_current(second)

    assert await store.reconcile_current(first) == first_id

    cursor = await processing_connection.execute(
        "SELECT id, status, error_category FROM careerops_v2.processing_jobs"
    )
    rows = await cursor.fetchall()
    by_id = {str(row[0]): (str(row[1]), row[2]) for row in rows}
    assert by_id[str(first_id)] == ("pending", None)
    assert by_id[str(second_id)] == ("cancelled", "superseded")


@pytest.mark.asyncio
async def test_manual_cancelled_exact_work_is_not_implicitly_reactivated(
    processing_connection: psycopg.AsyncConnection[object],
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    store = PostgresProcessingJobStore(processing_connection)
    spec = _spec(vacancy_id, binding_id, "a")
    job_id = await store.reconcile_current(spec)

    claimed = await store.claim_next(worker_id="worker-a")
    assert claimed is not None
    await store.mark_running(claimed)
    await store.cancel(claimed, reason="operator.cancelled")

    assert await store.reconcile_current(spec) == job_id
    cursor = await processing_connection.execute(
        "SELECT status, error_category FROM careerops_v2.processing_jobs WHERE id = %s",
        (job_id,),
    )
    row = await cursor.fetchone()
    assert row == ("cancelled", "operator.cancelled")


@pytest.mark.asyncio
async def test_superseding_running_job_fences_stale_worker(
    processing_connection: psycopg.AsyncConnection[object],
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    store = PostgresProcessingJobStore(processing_connection)

    first_id = await store.reconcile_current(_spec(vacancy_id, binding_id, "a"))
    claimed = await store.claim_next(worker_id="worker-a", lease_seconds=300)
    assert claimed is not None
    assert claimed.id == first_id
    await store.mark_running(claimed)

    await store.reconcile_current(_spec(vacancy_id, binding_id, "b"))

    with pytest.raises(ProcessingJobLeaseLost):
        await store.succeed(
            claimed,
            result_artifact_uri="s3://careerops-artifacts/processing/result.json",
        )


@pytest.mark.asyncio
async def test_withdraw_pair_fences_inflight_worker(
    processing_connection: psycopg.AsyncConnection[object],
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    store = PostgresProcessingJobStore(processing_connection)

    job_id = await store.reconcile_current(_spec(vacancy_id, binding_id, "a"))
    claimed = await store.claim_next(worker_id="worker-a")
    assert claimed is not None
    await store.mark_running(claimed)

    withdrawn = await store.withdraw_pair(
        ProcessingPairKey(vacancy_id, binding_id),
        reason="reconciliation.withdrawn",
    )
    assert withdrawn == 1

    with pytest.raises(ProcessingJobLeaseLost):
        await store.renew_lease(claimed)

    cursor = await processing_connection.execute(
        "SELECT status, error_category FROM careerops_v2.processing_jobs WHERE id = %s",
        (job_id,),
    )
    row = await cursor.fetchone()
    assert row == ("cancelled", "reconciliation.withdrawn")


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_with_new_fencing_token(
    processing_connection: psycopg.AsyncConnection[object],
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    store = PostgresProcessingJobStore(processing_connection)

    job_id = await store.reconcile_current(_spec(vacancy_id, binding_id, "a"))
    first_claim = await store.claim_next(worker_id="worker-a", lease_seconds=300)
    assert first_claim is not None
    assert first_claim.id == job_id
    assert first_claim.lease_token is not None

    await processing_connection.execute(
        """
        UPDATE careerops_v2.processing_jobs
        SET leased_at = now() - interval '2 minutes',
            lease_expires_at = now() - interval '1 minute'
        WHERE id = %s
        """,
        (job_id,),
    )

    second_claim = await store.claim_next(worker_id="worker-b", lease_seconds=300)
    assert second_claim is not None
    assert second_claim.id == job_id
    assert second_claim.attempt_count == 2
    assert second_claim.lease_token is not None
    assert second_claim.lease_token != first_claim.lease_token

    with pytest.raises(ProcessingJobLeaseLost):
        await store.mark_running(first_claim)


@pytest.mark.asyncio
async def test_deferred_job_is_not_claimable_until_due(
    processing_connection: psycopg.AsyncConnection[object],
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    store = PostgresProcessingJobStore(processing_connection)

    job_id = await store.reconcile_current(_spec(vacancy_id, binding_id, "a"))
    claimed = await store.claim_next(worker_id="worker-a")
    assert claimed is not None
    await store.mark_running(claimed)
    await store.defer(
        claimed,
        error_category="reranker.unavailable",
        next_attempt_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    assert await store.claim_next(worker_id="worker-b") is None
    await processing_connection.execute(
        "UPDATE careerops_v2.processing_jobs SET next_attempt_at = now() - interval '1 second' "
        "WHERE id = %s",
        (job_id,),
    )

    reclaimed = await store.claim_next(worker_id="worker-b")
    assert reclaimed is not None
    assert reclaimed.id == job_id
    assert reclaimed.attempt_count == 2


@pytest.mark.asyncio
async def test_success_requires_artifact_and_clears_lease(
    processing_connection: psycopg.AsyncConnection[object],
) -> None:
    vacancy_id, binding_id = await _seed_pair(processing_connection)
    store = PostgresProcessingJobStore(processing_connection)

    job_id = await store.reconcile_current(_spec(vacancy_id, binding_id, "a"))
    claimed = await store.claim_next(worker_id="worker-a", lease_seconds=300)
    assert claimed is not None
    await store.mark_running(claimed)

    with pytest.raises(ValueError, match="s3://"):
        await store.succeed(claimed, result_artifact_uri="file:///tmp/result.json")

    artifact_uri = "s3://careerops-artifacts/processing/results/result.json"
    await store.succeed(claimed, result_artifact_uri=artifact_uri)

    cursor = await processing_connection.execute(
        """
        SELECT status, result_artifact_uri, lease_token, finished_at
        FROM careerops_v2.processing_jobs
        WHERE id = %s
        """,
        (job_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "succeeded"
    assert row[1] == artifact_uri
    assert row[2] is None
    assert row[3] is not None
