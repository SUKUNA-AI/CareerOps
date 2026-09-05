from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.pq import TransactionStatus
from support.hh import make_hh_vacancy

from careerops_contracts import RawVacancyRef
from careerops_integrations.hh.application_audit import (
    HHApplicationAuditService,
    HHApplicationBlocked,
)
from careerops_integrations.hh.application_claims import (
    ApplicationClaimStatus,
    ApplicationIdentity,
)
from careerops_integrations.hh.mapper import extract_operational, map_hh_vacancy
from careerops_integrations.hh.resume_sync import ReconciledResume, ResumeLifecycle
from careerops_integrations.hh.runtime import HHExternalWriteGuard, RuntimeMode
from careerops_storage.postgres import (
    PostgresApplicationClaimStore,
    reserve_observe_query_window,
    upsert_evaluation_work_item,
    upsert_observation_run,
    upsert_reconciled_resume,
    upsert_source_profile,
    upsert_vacancy,
    upsert_vacancy_observation,
)

POSTGRES_DSN = os.getenv("CAREEROPS_TEST_POSTGRES_DSN")
TEST_DATABASE = "careerops_integration_test"
NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
WRITE_GUARD = HHExternalWriteGuard(
    runtime_mode=RuntimeMode.APPLY,
    allow_external_writes=True,
)

pytestmark = [
    pytest.mark.integration_postgres,
    pytest.mark.skipif(
        not POSTGRES_DSN,
        reason="CAREEROPS_TEST_POSTGRES_DSN is not configured",
    ),
]


async def _recreate_database(admin_dsn: str) -> None:
    admin = await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True)
    async with admin:
        await admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)')
        await admin.execute(
            f'CREATE DATABASE "{TEST_DATABASE}" TEMPLATE template0 ENCODING \'UTF8\''
        )


async def _drop_database(admin_dsn: str) -> None:
    admin = await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True)
    async with admin:
        await admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)')


async def _apply_migrations(dsn: str) -> None:
    migration_root = Path(__file__).parents[1] / "sql" / "migrations"
    migrations = sorted(migration_root.glob("000[1-5]_*.sql"))
    assert [path.name[:4] for path in migrations] == ["0001", "0002", "0003", "0004", "0005"]

    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    async with conn:
        for migration in migrations:
            await conn.execute(migration.read_text(encoding="utf-8"))


@pytest_asyncio.fixture
async def clean_postgres_dsn() -> AsyncIterator[str]:
    assert POSTGRES_DSN is not None
    params = conninfo_to_dict(POSTGRES_DSN)
    configured_host = params.get("host")
    if configured_host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("CAREEROPS_TEST_POSTGRES_DSN must address local PostgreSQL")
    configured_database = params.get("dbname", "postgres")
    if configured_database not in {"postgres", TEST_DATABASE}:
        raise RuntimeError(
            "CAREEROPS_TEST_POSTGRES_DSN must address postgres or "
            f"the disposable {TEST_DATABASE!r} database"
        )
    admin_dsn = make_conninfo(**{**params, "dbname": "postgres"})
    test_dsn = make_conninfo(**{**params, "dbname": TEST_DATABASE})

    await _recreate_database(admin_dsn)
    try:
        await _apply_migrations(test_dsn)
        yield test_dsn
    finally:
        await _drop_database(admin_dsn)


def _vacancy(vacancy_id: str) -> dict[str, Any]:
    return make_hh_vacancy(
        vacancy_id=vacancy_id,
        title=f"ML Engineer {vacancy_id}",
        description="<p>Python and PostgreSQL</p>",
        area={"id": "1", "name": "Москва"},
        published_at="2026-09-02T10:00:00+0300",
    )


def _resume(profile: str, resume_id: str) -> ReconciledResume:
    return ReconciledResume(
        source_profile=profile,
        source_resume_id=resume_id,
        current_title=f"Resume {resume_id}",
        upstream_status="published",
        lifecycle=ResumeLifecycle.ACTIVE,
        first_seen_at=NOW,
        last_seen_at=NOW,
        binding_key=f"binding-{resume_id}",
        binding_enabled=True,
        target_key="ml-target",
        query_sets=("ml_core",),
        auto_apply=True,
        binding_version=1,
        content_sha256="c" * 64,
        source_payload={"id": resume_id, "status": {"id": "published"}},
    )


async def _seed_resumes(
    conn: AsyncConnection[Any],
    *,
    profile: str,
    account_key: str,
    resume_ids: Sequence[str],
) -> tuple[int, dict[str, int]]:
    async with conn.transaction():
        profile_id = await upsert_source_profile(
            conn,
            source="hh",
            profile_key=profile,
            account_key=account_key,
        )
        database_ids = {
            resume_id: await upsert_reconciled_resume(
                conn,
                source_profile_id=profile_id,
                resume=_resume(profile, resume_id),
            )
            for resume_id in resume_ids
        }
    return profile_id, database_ids


async def _scalar(
    conn: AsyncConnection[Any],
    query: str,
    params: tuple[Any, ...] = (),
) -> Any:
    cursor = await conn.execute(query, params)
    row = await cursor.fetchone()
    assert row is not None
    return row[0]


@dataclass(frozen=True, slots=True)
class _AuditRef:
    uri: str
    sha256: str


class _AuditStore:
    async def put_json(
        self,
        key: str,
        payload: Any,
        *,
        collected_at: datetime | None = None,
    ) -> _AuditRef:
        del collected_at
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return _AuditRef(
            uri=f"s3://integration-test/{key}",
            sha256=hashlib.sha256(body).hexdigest(),
        )


class _ApplicationDriver:
    def __init__(
        self,
        vacancy: dict[str, Any],
        conn: AsyncConnection[Any],
    ) -> None:
        self.vacancy = vacancy
        self.conn = conn
        self.submitted: set[tuple[str, str]] = set()
        self.submit_calls = 0

    def _assert_no_database_transaction(self) -> None:
        assert self.conn.info.transaction_status is TransactionStatus.IDLE

    def fetch_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        self._assert_no_database_transaction()
        assert vacancy_id == str(self.vacancy["id"])
        return dict(self.vacancy)

    def find_application_evidence(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
    ) -> dict[str, Any]:
        self._assert_no_database_transaction()
        return {
            "source_profile": "profile-apply",
            "source_resume_id": resume_id,
            "vacancy_id": vacancy_id,
            "found": (resume_id, vacancy_id) in self.submitted,
        }

    def submit_application(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> dict[str, Any]:
        self._assert_no_database_transaction()
        del message
        self.submit_calls += 1
        self.submitted.add((resume_id, vacancy_id))
        return {"id": f"negotiation-{self.submit_calls}"}

    def submit_application_with_test(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> dict[str, Any]:
        return self.submit_application(
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            message=message,
        )


def _application_service(
    *,
    conn: AsyncConnection[Any],
    driver: _ApplicationDriver,
    account_key: str,
) -> HHApplicationAuditService:
    return HHApplicationAuditService(
        driver=driver,
        store=_AuditStore(),
        claim_store=PostgresApplicationClaimStore(conn),
        account_key=account_key,
        profile_id="profile-apply",
        external_write_guard=WRITE_GUARD,
    )


@pytest.mark.asyncio
async def test_apply_materializes_vacancy_before_resume_specific_claim(
    clean_postgres_dsn: str,
) -> None:
    conn = await psycopg.AsyncConnection.connect(clean_postgres_dsn, autocommit=True)
    async with conn:
        await _seed_resumes(
            conn,
            profile="profile-apply",
            account_key="junior",
            resume_ids=("resume-first", "resume-second"),
        )
        driver = _ApplicationDriver(_vacancy("123"), conn)

        assert await _scalar(
            conn,
            "SELECT count(*) FROM careerops.vacancies WHERE source_entity_id = %s",
            ("123",),
        ) == 0
        first = await _application_service(
            conn=conn,
            driver=driver,
            account_key="junior",
        ).apply(vacancy_id="123", resume_id="resume-first", message="first")
        assert first.claim_status is ApplicationClaimStatus.SUBMITTED
        assert driver.submit_calls == 1
        assert await _scalar(
            conn,
            "SELECT count(*) FROM careerops.vacancies WHERE source_entity_id = %s",
            ("123",),
        ) == 1

        with pytest.raises(HHApplicationBlocked, match="persistent claim"):
            await _application_service(
                conn=conn,
                driver=driver,
                account_key="junior_main",
            ).apply(vacancy_id="123", resume_id="resume-first", message="duplicate")
        assert driver.submit_calls == 1
        assert await _scalar(
            conn,
            "SELECT count(*) FROM careerops.vacancies WHERE source_entity_id = %s",
            ("123",),
        ) == 1

        second = await _application_service(
            conn=conn,
            driver=driver,
            account_key="junior",
        ).apply(vacancy_id="123", resume_id="resume-second", message="second")
        assert second.claim_status is ApplicationClaimStatus.SUBMITTED
        assert driver.submit_calls == 2
        assert await _scalar(
            conn,
            "SELECT count(*) FROM careerops.application_claims WHERE vacancy_id = "
            "(SELECT id FROM careerops.vacancies WHERE source_entity_id = %s)",
            ("123",),
        ) == 2


async def _prepare_claim(
    store: PostgresApplicationClaimStore,
    *,
    identity: ApplicationIdentity,
    account_key: str,
) -> None:
    await store.prepare_identity(
        identity=identity,
        account_key=account_key,
        vacancy=_vacancy(identity.vacancy_id),
        observed_at=NOW,
        raw_uri=f"s3://integration-test/{identity.vacancy_id}.json",
        content_hash="d" * 64,
    )


@pytest.mark.asyncio
async def test_claim_retry_and_non_retryable_states(clean_postgres_dsn: str) -> None:
    conn = await psycopg.AsyncConnection.connect(clean_postgres_dsn, autocommit=True)
    async with conn:
        await _seed_resumes(
            conn,
            profile="profile-states",
            account_key="states",
            resume_ids=("resume",),
        )
        store = PostgresApplicationClaimStore(conn)
        cases = (
            ("201", ApplicationClaimStatus.FAILED_SAFE_TO_RETRY, True),
            ("202", ApplicationClaimStatus.SUBMITTING, False),
            ("203", ApplicationClaimStatus.SUBMITTED, False),
            ("204", ApplicationClaimStatus.UNCERTAIN, False),
        )
        for vacancy_id, status, retry_allowed in cases:
            identity = ApplicationIdentity("profile-states", "resume", vacancy_id)
            await _prepare_claim(store, identity=identity, account_key="states")
            first_run = uuid4()
            first = await store.acquire(
                identity=identity,
                account_key="states",
                application_run_id=first_run,
                claimed_at=NOW,
            )
            assert first.acquired is True
            await store.transition(
                identity=identity,
                application_run_id=first_run,
                expected=(ApplicationClaimStatus.CLAIMED,),
                status=status,
                changed_at=NOW,
            )

            second = await store.acquire(
                identity=identity,
                account_key="states-renamed",
                application_run_id=uuid4(),
                claimed_at=NOW,
            )
            assert second.acquired is retry_allowed
            if retry_allowed:
                assert second.record.status is ApplicationClaimStatus.CLAIMED
                assert second.record.attempt_count == 2
            else:
                assert second.record.status is status


@pytest.mark.asyncio
async def test_concurrent_claim_has_exactly_one_winner(clean_postgres_dsn: str) -> None:
    setup = await psycopg.AsyncConnection.connect(clean_postgres_dsn, autocommit=True)
    async with setup:
        await _seed_resumes(
            setup,
            profile="profile-concurrent",
            account_key="concurrent",
            resume_ids=("resume",),
        )
        identity = ApplicationIdentity("profile-concurrent", "resume", "301")
        await _prepare_claim(
            PostgresApplicationClaimStore(setup),
            identity=identity,
            account_key="concurrent",
        )

    first_conn = await psycopg.AsyncConnection.connect(clean_postgres_dsn, autocommit=True)
    second_conn = await psycopg.AsyncConnection.connect(clean_postgres_dsn, autocommit=True)
    async with first_conn, second_conn:
        first_store = PostgresApplicationClaimStore(first_conn)
        second_store = PostgresApplicationClaimStore(second_conn)
        results = await asyncio.gather(
            first_store.acquire(
                identity=identity,
                account_key="concurrent",
                application_run_id=uuid4(),
                claimed_at=NOW,
            ),
            second_store.acquire(
                identity=identity,
                account_key="concurrent-renamed",
                application_run_id=uuid4(),
                claimed_at=NOW,
            ),
        )
        assert [result.acquired for result in results].count(True) == 1
        assert [result.acquired for result in results].count(False) == 1


class _InjectedMaterializationFailure(RuntimeError):
    pass


async def _materialize_observe_run(
    conn: AsyncConnection[Any],
    *,
    run_id: UUID,
    profile: str,
    account_key: str,
    vacancy_id: str,
    fail_after_first_evaluation: bool = False,
) -> None:
    async with conn.transaction():
        profile_id, resume_ids = await _seed_resumes(
            conn,
            profile=profile,
            account_key=account_key,
            resume_ids=("resume-a", "resume-b"),
        )
        payload = _vacancy(vacancy_id)
        raw = RawVacancyRef(
            source="hh",
            source_entity_id=vacancy_id,
            raw_uri=f"s3://integration-test/{run_id}/vacancy.json",
            content_hash="e" * 64,
            collected_at=NOW,
        )
        vacancy_database_id = await upsert_vacancy(
            conn,
            vacancy=map_hh_vacancy(payload, raw=raw),
            operational=extract_operational(payload),
            source_employer_id="10",
        )
        await upsert_observation_run(
            conn,
            run_id=run_id,
            source_profile_id=profile_id,
            account_key=account_key,
            status="finished",
            query_set_keys=("ml_core",),
            query_keys=("query-a", "query-b"),
            query_catalog_size=2,
            query_catalog_signature="f" * 64,
            max_queries_per_run=2,
            query_cursor_start=0,
            query_cursor_next=0,
            query_rotation_wrapped=True,
            pages=1,
            per_page=50,
            max_unique_vacancies=250,
            max_full_fetches=100,
            search_delay_seconds=1.0,
            full_fetch_min_delay_seconds=1.5,
            full_fetch_max_delay_seconds=3.0,
            started_at=NOW,
            finished_at=NOW,
            s3_prefix=f"batches/run_id={run_id}",
            search_observation_count=1,
            unique_vacancy_count=1,
            candidate_count=1,
            full_fetch_attempted=1,
            full_fetched=1,
            evaluation_candidate_count=2,
            failed=0,
            stopped_on_captcha=False,
        )
        await upsert_vacancy_observation(
            conn,
            run_id=run_id,
            vacancy_id=vacancy_database_id,
            full_fetch_status="fetched",
            matched_query_keys=("query-a",),
            matched_query_sets=("ml_core",),
            query_page_uris=(f"s3://integration-test/{run_id}/page.json",),
            search_item_uri=f"s3://integration-test/{run_id}/search-item.json",
            vacancy_uri=raw.raw_uri,
            evaluation_candidates_uri=(
                f"s3://integration-test/{run_id}/evaluation-candidates.json"
            ),
            observed_at=NOW,
        )
        for index, resume_database_id in enumerate(resume_ids.values()):
            await upsert_evaluation_work_item(
                conn,
                run_id=run_id,
                vacancy_id=vacancy_database_id,
                resume_id=resume_database_id,
                binding_key=f"binding-{index}",
                target_key="ml-target",
                binding_version=1,
                auto_apply=True,
                matched_query_keys=("query-a",),
                matched_query_sets=("ml_core",),
                resume_query_sets=("ml_core",),
                overlap_query_keys=("query-a",),
                overlap_query_sets=("ml_core",),
                has_provenance_overlap=True,
                full_fetch_status="fetched",
                evaluation_status="pending_filtering_v2",
                created_at=NOW,
            )
            if fail_after_first_evaluation and index == 0:
                raise _InjectedMaterializationFailure("injected midway failure")


@pytest.mark.asyncio
async def test_observe_replay_and_complete_transaction_rollback(
    clean_postgres_dsn: str,
) -> None:
    conn = await psycopg.AsyncConnection.connect(clean_postgres_dsn, autocommit=True)
    async with conn:
        run_id = UUID("11111111-1111-4111-8111-111111111111")
        kwargs = {
            "run_id": run_id,
            "profile": "profile-observe",
            "account_key": "observe",
            "vacancy_id": "401",
        }
        await _materialize_observe_run(conn, **kwargs)
        await _materialize_observe_run(conn, **kwargs)
        assert await _scalar(
            conn,
            "SELECT count(*) FROM careerops.observation_runs WHERE id = %s",
            (run_id,),
        ) == 1
        assert await _scalar(
            conn,
            "SELECT count(*) FROM careerops.vacancy_observations WHERE run_id = %s",
            (run_id,),
        ) == 1
        assert await _scalar(
            conn,
            "SELECT count(*) FROM careerops.evaluation_work_items WHERE run_id = %s",
            (run_id,),
        ) == 2

        failed_run_id = UUID("22222222-2222-4222-8222-222222222222")
        with pytest.raises(_InjectedMaterializationFailure):
            await _materialize_observe_run(
                conn,
                run_id=failed_run_id,
                profile="profile-observe-failed",
                account_key="observe-failed",
                vacancy_id="402",
                fail_after_first_evaluation=True,
            )
        assert await _scalar(
            conn,
            "SELECT count(*) FROM careerops.source_profiles WHERE profile_key = %s",
            ("profile-observe-failed",),
        ) == 0
        assert await _scalar(
            conn,
            "SELECT count(*) FROM careerops.vacancies WHERE source_entity_id = %s",
            ("402",),
        ) == 0
        assert await _scalar(
            conn,
            "SELECT count(*) FROM careerops.observation_runs WHERE id = %s",
            (failed_run_id,),
        ) == 0
        assert await _scalar(
            conn,
            "SELECT count(*) FROM careerops.vacancy_observations WHERE run_id = %s",
            (failed_run_id,),
        ) == 0
        assert await _scalar(
            conn,
            "SELECT count(*) FROM careerops.evaluation_work_items WHERE run_id = %s",
            (failed_run_id,),
        ) == 0


@pytest.mark.asyncio
async def test_observe_query_cursor_reservation_uses_real_psycopg(
    clean_postgres_dsn: str,
) -> None:
    conn = await psycopg.AsyncConnection.connect(
        clean_postgres_dsn,
        autocommit=True,
    )
    async with conn:
        first = await reserve_observe_query_window(
            conn,
            source_profile="profile-cursor",
            account_key="cursor",
            catalog_signature="a" * 64,
            catalog_size=272,
            max_queries=50,
            run_id=uuid4(),
            reserved_at=NOW,
        )

        assert first.window_start == 0
        assert first.window_size == 50
        assert first.next_query_offset == 50

        second = await reserve_observe_query_window(
            conn,
            source_profile="profile-cursor",
            account_key="cursor",
            catalog_signature="a" * 64,
            catalog_size=272,
            max_queries=50,
            run_id=uuid4(),
            reserved_at=NOW,
        )

        assert second.window_start == 50
        assert second.window_size == 50
        assert second.next_query_offset == 100
