from __future__ import annotations

import re
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import UUID

import pytest

from careerops_contracts import CanonicalVacancy
from careerops_integrations.hh.application_claims import (
    ApplicationClaimIdentityNotMaterialized,
    ApplicationClaimStatus,
    ApplicationClaimTransitionError,
    ApplicationIdentity,
)
from careerops_integrations.hh.models import HHVacancyOperational
from careerops_integrations.hh.resume_sync import ReconciledResume, ResumeLifecycle
from careerops_storage.postgres import (
    PostgresResumeRegistry,
    acquire_application_claim,
    prepare_application_claim_identity,
    reserve_observe_query_window,
    transition_application_claim,
    upsert_application,
    upsert_batch_run,
    upsert_evaluation_work_item,
    upsert_observation_run,
    upsert_partial_vacancy,
    upsert_reconciled_resume,
    upsert_resume,
    upsert_source_profile,
    upsert_vacancy,
    upsert_vacancy_decision,
    upsert_vacancy_observation,
)

NOW = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")


class FakeCursor:
    def __init__(
        self,
        row: tuple[Any, ...] | list[tuple[Any, ...]] | None,
    ) -> None:
        self._row = row

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._row if isinstance(self._row, tuple) else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._row if isinstance(self._row, list) else []


class FakeConnection:
    def __init__(
        self,
        row: tuple[Any, ...] | list[tuple[Any, ...]] | None,
    ) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, params: tuple[Any, ...]) -> FakeCursor:
        assert query.count("%s") == len(params)
        self.calls.append((query, params))
        return FakeCursor(self.row)


class SequencedFakeConnection(FakeConnection):
    def __init__(
        self,
        rows: list[tuple[Any, ...] | None],
    ) -> None:
        super().__init__(None)
        self.rows = iter(rows)

    async def execute(self, query: str, params: tuple[Any, ...]) -> FakeCursor:
        assert query.count("%s") == len(params)
        self.calls.append((query, params))
        return FakeCursor(next(self.rows))


class FakeTransaction(AbstractAsyncContextManager[None]):
    def __init__(self, connection: TransactionalSequencedFakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> None:
        self.connection.events.append("begin")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_value, traceback
        self.connection.events.append("rollback" if exc_type else "commit")
        return False


class TransactionalSequencedFakeConnection(SequencedFakeConnection):
    def __init__(self, rows: list[tuple[Any, ...] | None]) -> None:
        super().__init__(rows)
        self.events: list[str] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)


def _assert_sql_call(conn: FakeConnection, table: str) -> tuple[str, tuple[Any, ...]]:
    query, params = conn.calls[-1]
    assert f"INSERT INTO careerops.{table}" in query
    assert "ON CONFLICT" in query
    assert "RETURNING" in query
    assert query.count("%s") == len(params)
    return query, params


def _canonical() -> CanonicalVacancy:
    return CanonicalVacancy(
        source="hh",
        source_entity_id="123",
        title="ML Engineer",
        company_name="Example",
        description="Python",
        source_url="https://hh.ru/vacancy/123",
        published_at=NOW,
        collected_at=NOW,
        raw_uri="s3://careerops-raw/_lab/hh/vacancy.json",
        content_hash="a" * 64,
    )


def _operational(vacancy_id: str = "123") -> HHVacancyOperational:
    return HHVacancyOperational(
        vacancy_id=vacancy_id,
        relations=("got_response",),
        archived=False,
        closed_for_applicants=False,
        has_test=False,
        response_letter_required=True,
    )


def _hh_vacancy() -> dict[str, Any]:
    return {
        "id": "123",
        "name": "ML Engineer",
        "description": "<p>Python</p>",
        "alternate_url": "https://hh.ru/vacancy/123",
        "employer": {"id": "10", "name": "Example"},
        "area": {"name": "Москва"},
        "relations": [],
        "archived": False,
        "closed_for_applicants": False,
        "has_test": False,
        "response_letter_required": False,
        "response_url": None,
    }


@pytest.mark.asyncio
async def test_upsert_source_profile_and_resume_have_idempotent_sql_intent() -> None:
    conn = FakeConnection((42,))
    assert (
        await upsert_source_profile(
            conn,  # type: ignore[arg-type]
            source="hh",
            profile_key="careerops-ml",
        )
        == 42
    )
    _, params = _assert_sql_call(conn, "source_profiles")
    assert params == ("hh", "careerops-ml", None)

    conn.row = (77,)
    assert (
        await upsert_resume(
            conn,  # type: ignore[arg-type]
            source_profile_id=42,
            source_resume_id="resume-123",
            title="ML Engineer",
            observed_at=NOW,
        )
        == 77
    )
    query, params = _assert_sql_call(conn, "resumes")
    assert params[:3] == (42, "resume-123", "ML Engineer")
    assert "LEAST(r.first_seen_at, EXCLUDED.first_seen_at)" in query
    assert "GREATEST(r.last_seen_at, EXCLUDED.last_seen_at)" in query


@pytest.mark.asyncio
async def test_partial_vacancy_upsert_only_updates_supported_fields() -> None:
    conn = FakeConnection((100,))
    result = await upsert_partial_vacancy(
        conn,  # type: ignore[arg-type]
        source="hh",
        source_entity_id="123",
        source_employer_id="10",
        title="ML Engineer",
        company_name="Example",
        location="Москва",
        source_url="https://hh.ru/vacancy/123",
        published_at=NOW,
        observed_at=NOW,
        raw_uri="s3://careerops-raw/_lab/hh/search_item.json",
        content_hash="a" * 64,
    )
    assert result == 100
    query, params = _assert_sql_call(conn, "vacancies")
    assert "description" not in query.split("ON CONFLICT", 1)[0]
    assert "EXCLUDED.last_seen_at >= v.last_seen_at" in query
    assert params[0:2] == ("hh", "123")


@pytest.mark.asyncio
async def test_full_vacancy_upsert_is_newest_observation_wins() -> None:
    conn = FakeConnection((101,))
    result = await upsert_vacancy(
        conn,  # type: ignore[arg-type]
        vacancy=_canonical(),
        operational=_operational(),
        source_employer_id="10",
    )
    assert result == 101
    query, params = _assert_sql_call(conn, "vacancies")
    assert "EXCLUDED.last_seen_at >= v.last_seen_at" in query
    assert "latest_raw_uri" in query
    assert params[0:3] == ("hh", "123", "10")
    assert params[14] == ["got_response"]


@pytest.mark.asyncio
async def test_full_vacancy_rejects_contract_id_mismatch_before_sql() -> None:
    conn = FakeConnection((101,))
    with pytest.raises(ValueError, match="IDs differ"):
        await upsert_vacancy(
            conn,  # type: ignore[arg-type]
            vacancy=_canonical(),
            operational=_operational("different"),
        )
    assert conn.calls == []


@pytest.mark.asyncio
async def test_batch_decision_and_application_sql_match_conflict_keys() -> None:
    conn = FakeConnection((str(RUN_ID),))
    assert (
        await upsert_batch_run(
            conn,  # type: ignore[arg-type]
            run_id=RUN_ID,
            resume_id=77,
            search_query="ML",
            area_id="1",
            period_days=14,
            pages=1,
            per_page=50,
            max_responses=20,
            professional_roles=["96"],
            cover_letter_mode="vacancy_template_v1",
            live=True,
            started_at=NOW,
        )
        == RUN_ID
    )
    batch_query, _ = _assert_sql_call(conn, "batch_runs")
    assert "ON CONFLICT (id)" in batch_query
    assert "profile_id" not in batch_query
    assert "br.status = 'finished' AND EXCLUDED.status = 'incomplete'" in batch_query
    assert "COALESCE(EXCLUDED.discovered, br.discovered)" in batch_query

    conn.row = (500,)
    assert (
        await upsert_vacancy_decision(
            conn,  # type: ignore[arg-type]
            run_id=RUN_ID,
            vacancy_id=101,
            stage="full_vacancy_validation",
            accepted=True,
            reason="accepted",
            matched_domains=["ml"],
            created_at=NOW,
        )
        == 500
    )
    decision_query, _ = _assert_sql_call(conn, "vacancy_decisions")
    assert "ON CONFLICT (" in decision_query
    assert "run_id," in decision_query
    assert "vacancy_id," in decision_query
    assert "stage" in decision_query

    conn.row = (600,)
    assert (
        await upsert_application(
            conn,  # type: ignore[arg-type]
            application_run_id=UUID("22222222-2222-4222-8222-222222222222"),
            batch_run_id=RUN_ID,
            vacancy_id=101,
            resume_id=77,
            submission_mode="negotiations_api",
            status="submitted",
            confirmed=True,
            requested_at=NOW,
            finished_at=NOW,
        )
        == 600
    )
    application_query, _ = _assert_sql_call(conn, "applications")
    assert "ON CONFLICT (vacancy_id, resume_id)" in application_query
    assert "application_run_id = CASE" in application_query
    assert "batch_run_id = CASE" in application_query
    assert "EXCLUDED.requested_at >= a.requested_at" in application_query


@pytest.mark.asyncio
async def test_reconciled_resume_persists_lifecycle_binding_and_selectability() -> None:
    conn = FakeConnection((77,))
    resume = ReconciledResume(
        source_profile="profile",
        source_resume_id="resume-123",
        current_title="ML Engineer",
        upstream_status="published",
        lifecycle=ResumeLifecycle.ACTIVE,
        first_seen_at=NOW,
        last_seen_at=NOW,
        binding_key="ml",
        binding_enabled=True,
        target_key="ml-target",
        query_sets=("ml_core",),
        auto_apply=True,
        binding_version=3,
        content_sha256="a" * 64,
        source_payload={"id": "resume-123", "status": {"id": "published"}},
    )

    result = await upsert_reconciled_resume(
        conn,  # type: ignore[arg-type]
        source_profile_id=42,
        resume=resume,
    )

    assert result == 77
    query, params = _assert_sql_call(conn, "resumes")
    assert "upstream_status" in query
    assert "selectable_for_auto_apply" in query
    assert params[6:10] == ("published", "active", True, None)
    assert params[15:17] == (True, True)


@pytest.mark.asyncio
async def test_postgres_resume_registry_rehydrates_primary_runtime_state() -> None:
    conn = FakeConnection(
        [
            (
                "resume-123",
                "ML Engineer",
                "published",
                "active",
                NOW,
                NOW,
                None,
                "ml",
                True,
                "ml-target",
                ["ml_core"],
                True,
                3,
                "a" * 64,
                {"id": "resume-123", "status": {"id": "published"}},
            )
        ]
    )

    inventory = await PostgresResumeRegistry(conn).load(  # type: ignore[arg-type]
        account_key="account",
        source_profile="profile",
    )

    assert inventory is not None
    resume = inventory.by_source_id["resume-123"]
    assert resume.upstream_status == "published"
    assert resume.binding_key == "ml"
    assert resume.selectable_for_auto_apply is True
    query, params = conn.calls[0]
    assert "sp.account_key = %s" in query
    assert params == ("profile", "account")


def _claim_row(
    *,
    status: ApplicationClaimStatus,
    run_id: UUID,
) -> tuple[Any, ...]:
    return (
        "account",
        str(run_id),
        status.value,
        1,
        NOW,
        NOW,
    )


def test_application_claim_migration_uses_canonical_oltp_ids() -> None:
    migration = Path("sql/migrations/0003_add_hh_application_claims.sql").read_text(
        encoding="utf-8"
    )
    assert "resume_id bigint NOT NULL REFERENCES careerops.resumes (id)" in migration
    assert "vacancy_id bigint NOT NULL REFERENCES careerops.vacancies (id)" in migration
    unique_clause = migration.split(
        "CONSTRAINT application_claims_identity_uk",
        1,
    )[1].split(",\n    CONSTRAINT application_claims_status_ck", 1)[0]
    assert "UNIQUE (resume_id, vacancy_id)" in unique_clause
    assert "account_key" not in unique_clause


@pytest.mark.asyncio
async def test_application_claim_acquisition_is_atomic_and_resume_specific() -> None:
    run_id = UUID("33333333-3333-4333-8333-333333333333")
    identity = ApplicationIdentity("profile", "resume", "vacancy")
    conn = SequencedFakeConnection(
        [
            (41, 73),
            _claim_row(status=ApplicationClaimStatus.CLAIMED, run_id=run_id),
        ]
    )

    acquisition = await acquire_application_claim(
        conn,  # type: ignore[arg-type]
        identity=identity,
        account_key="account",
        application_run_id=run_id,
        claimed_at=NOW,
    )

    assert acquisition.acquired is True
    query, params = _assert_sql_call(conn, "application_claims")
    conflict_target = query.split("ON CONFLICT", 1)[1].split("DO UPDATE", 1)[0]
    assert "resume_id, vacancy_id" in conflict_target
    assert "account_key" not in conflict_target
    assert "WHERE ac.status = 'FAILED_SAFE_TO_RETRY'" in query
    assert params[1:5] == (
        "account",
        41,
        73,
        run_id,
    )
    resolve_query, resolve_params = conn.calls[0]
    assert "JOIN careerops.resumes" in resolve_query
    assert "JOIN careerops.vacancies" in resolve_query
    assert resolve_params == ("vacancy", "profile", "resume")


@pytest.mark.asyncio
async def test_application_claim_fails_closed_when_oltp_identity_is_missing() -> None:
    identity = ApplicationIdentity("profile", "resume", "vacancy")
    conn = FakeConnection(None)

    with pytest.raises(
        ApplicationClaimIdentityNotMaterialized,
        match="not materialized",
    ):
        await acquire_application_claim(
            conn,  # type: ignore[arg-type]
            identity=identity,
            account_key="account",
            application_run_id=RUN_ID,
            claimed_at=NOW,
        )

    assert len(conn.calls) == 1
    assert "INSERT INTO careerops.application_claims" not in conn.calls[0][0]


@pytest.mark.asyncio
async def test_application_preparation_requires_resume_then_upserts_vacancy() -> None:
    identity = ApplicationIdentity("profile", "resume", "123")
    conn = SequencedFakeConnection([(41,), (73,)])

    await prepare_application_claim_identity(
        conn,  # type: ignore[arg-type]
        identity=identity,
        account_key="account",
        vacancy=_hh_vacancy(),
        observed_at=NOW,
        raw_uri="s3://bucket/vacancy-before.json",
        content_hash="b" * 64,
    )

    resume_query, resume_params = conn.calls[0]
    assert "JOIN careerops.resumes" in resume_query
    assert "r.lifecycle = 'active'" in resume_query
    assert "r.present_in_upstream" in resume_query
    assert resume_params == ("profile", "resume")
    vacancy_query, vacancy_params = conn.calls[1]
    assert "INSERT INTO careerops.vacancies" in vacancy_query
    assert "ON CONFLICT (source, source_entity_id)" in vacancy_query
    assert vacancy_params[0:3] == ("hh", "123", "10")
    assert vacancy_params[-2:] == (
        "s3://bucket/vacancy-before.json",
        "b" * 64,
    )


@pytest.mark.asyncio
async def test_application_preparation_does_not_invent_missing_resume() -> None:
    identity = ApplicationIdentity("profile", "missing", "123")
    conn = FakeConnection(None)

    with pytest.raises(
        ApplicationClaimIdentityNotMaterialized,
        match="resume identity is not current",
    ):
        await prepare_application_claim_identity(
            conn,  # type: ignore[arg-type]
            identity=identity,
            account_key="account",
            vacancy=_hh_vacancy(),
            observed_at=NOW,
            raw_uri="s3://bucket/vacancy-before.json",
            content_hash="b" * 64,
        )

    assert len(conn.calls) == 1
    assert "INSERT INTO careerops.vacancies" not in conn.calls[0][0]


@pytest.mark.asyncio
async def test_application_claim_transition_fails_on_stale_owner_or_state() -> None:
    identity = ApplicationIdentity("profile", "resume", "vacancy")
    conn = SequencedFakeConnection([(41, 73), None])

    with pytest.raises(ApplicationClaimTransitionError, match="changed concurrently"):
        await transition_application_claim(
            conn,  # type: ignore[arg-type]
            identity=identity,
            application_run_id=RUN_ID,
            expected=(ApplicationClaimStatus.CLAIMED,),
            status=ApplicationClaimStatus.SUBMITTING,
            changed_at=NOW,
        )

    query, _ = conn.calls[-1]
    assert "ac.resume_id = %s" in query
    assert "ac.vacancy_id = %s" in query
    assert "ac.account_key = %s" not in query
    assert "ac.application_run_id = %s" in query
    assert "ac.status = ANY(%s)" in query


@pytest.mark.asyncio
async def test_observe_query_window_is_reserved_atomically_by_source_profile() -> None:
    signature = "a" * 64
    conn = TransactionalSequencedFakeConnection(
        [
            (17,),
            (signature, 366, 50, 50, 100),
        ]
    )

    reservation = await reserve_observe_query_window(
        conn,  # type: ignore[arg-type]
        source_profile="stable-hh-profile",
        account_key="junior_main",
        catalog_signature=signature,
        catalog_size=366,
        max_queries=50,
        run_id=RUN_ID,
        reserved_at=NOW,
    )

    assert conn.events == ["begin", "commit"]
    assert reservation.source_profile == "stable-hh-profile"
    assert reservation.account_key == "junior_main"
    assert reservation.window_start == 50
    assert reservation.window_size == 50
    assert reservation.next_query_offset == 100
    profile_query, profile_params = conn.calls[0]
    assert "ON CONFLICT (source, profile_key)" in profile_query
    assert profile_params == ("hh", "stable-hh-profile", "junior_main")
    cursor_query, cursor_params = conn.calls[1]
    conflict_target = cursor_query.split("ON CONFLICT", 1)[1].split(
        "DO UPDATE",
        1,
    )[0]
    assert "source_profile_id" in conflict_target
    assert "account_key" not in conflict_target
    assert "oqc.next_query_offset" in cursor_query
    assert cursor_params == (
        17,
        "junior_main",
        signature,
        366,
        50,
        50,
        RUN_ID,
        NOW,
    )


@pytest.mark.asyncio
async def test_observation_upserts_use_stable_run_entity_conflict_keys() -> None:
    run_conn = FakeConnection((str(RUN_ID),))
    await upsert_observation_run(
        run_conn,  # type: ignore[arg-type]
        run_id=RUN_ID,
        source_profile_id=17,
        account_key="junior",
        status="finished",
        query_set_keys=["ml_core"],
        query_keys=["query-1", "query-2"],
        query_catalog_size=5,
        query_catalog_signature="a" * 64,
        max_queries_per_run=2,
        query_cursor_start=0,
        query_cursor_next=2,
        query_rotation_wrapped=False,
        pages=1,
        per_page=50,
        max_unique_vacancies=250,
        max_full_fetches=100,
        search_delay_seconds=1.0,
        full_fetch_min_delay_seconds=1.5,
        full_fetch_max_delay_seconds=3.0,
        started_at=NOW,
        finished_at=NOW,
        s3_prefix="batches/date=2026-08-30/run_id=run",
    )
    run_query, _ = _assert_sql_call(run_conn, "observation_runs")
    assert "ON CONFLICT (id)" in run_query

    observation_conn = FakeConnection((str(RUN_ID),))
    await upsert_vacancy_observation(
        observation_conn,  # type: ignore[arg-type]
        run_id=RUN_ID,
        vacancy_id=73,
        full_fetch_status="fetched",
        matched_query_keys=["query-1"],
        matched_query_sets=["ml_core"],
        query_page_uris=["s3://bucket/page.json"],
        search_item_uri="s3://bucket/search_item.json",
        vacancy_uri="s3://bucket/vacancy.json",
        evaluation_candidates_uri="s3://bucket/evaluations.json",
        observed_at=NOW,
    )
    observation_query, _ = _assert_sql_call(
        observation_conn,
        "vacancy_observations",
    )
    assert "ON CONFLICT (run_id, vacancy_id)" in observation_query

    evaluation_conn = FakeConnection((str(RUN_ID),))
    await upsert_evaluation_work_item(
        evaluation_conn,  # type: ignore[arg-type]
        run_id=RUN_ID,
        vacancy_id=73,
        resume_id=41,
        binding_key="ml",
        target_key="ml-target",
        binding_version=1,
        auto_apply=False,
        matched_query_keys=["query-1"],
        matched_query_sets=["ml_core"],
        resume_query_sets=["ml_core"],
        overlap_query_keys=["query-1"],
        overlap_query_sets=["ml_core"],
        has_provenance_overlap=True,
        full_fetch_status="fetched",
        evaluation_status="pending_filtering_v2",
        created_at=NOW,
    )
    evaluation_query, _ = _assert_sql_call(
        evaluation_conn,
        "evaluation_work_items",
    )
    assert "ON CONFLICT (run_id, vacancy_id, resume_id)" in evaluation_query


@pytest.mark.asyncio
async def test_insert_columns_are_declared_by_ordered_migrations() -> None:
    migration = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path("sql/migrations").glob("*.sql"))
    )
    table_columns: dict[str, set[str]] = {}
    for table, body in re.findall(
        r"CREATE TABLE IF NOT EXISTS careerops\.(\w+) \((.*?)\n\);",
        migration,
        flags=re.DOTALL,
    ):
        columns: set[str] = set()
        for line in body.splitlines():
            token = line.strip().split(maxsplit=1)[0].rstrip(",") if line.strip() else ""
            if token and token not in {"CONSTRAINT", "REFERENCES"}:
                columns.add(token)
        table_columns[table] = columns
    for table, alter_body in re.findall(
        r"ALTER TABLE careerops\.(\w+)(.*?);",
        migration,
        flags=re.DOTALL,
    ):
        for column in re.findall(
            r"ADD COLUMN(?: IF NOT EXISTS)? (\w+)",
            alter_body,
        ):
            table_columns.setdefault(table, set()).add(column)

    calls: list[tuple[str, tuple[Any, ...]]] = []
    source_conn = FakeConnection((1,))
    await upsert_source_profile(
        source_conn,  # type: ignore[arg-type]
        source="hh",
        profile_key="profile",
    )
    calls.extend(source_conn.calls)

    resume_conn = FakeConnection((2,))
    await upsert_resume(
        resume_conn,  # type: ignore[arg-type]
        source_profile_id=1,
        source_resume_id="resume",
        observed_at=NOW,
    )
    calls.extend(resume_conn.calls)

    partial_conn = FakeConnection((2,))
    await upsert_partial_vacancy(
        partial_conn,  # type: ignore[arg-type]
        source="hh",
        source_entity_id="1",
        observed_at=NOW,
        raw_uri="s3://bucket/key",
        content_hash="a" * 64,
    )
    calls.extend(partial_conn.calls)

    vacancy_conn = FakeConnection((3,))
    await upsert_vacancy(
        vacancy_conn,  # type: ignore[arg-type]
        vacancy=_canonical(),
        operational=_operational(),
    )
    calls.extend(vacancy_conn.calls)

    batch_conn = FakeConnection((str(RUN_ID),))
    await upsert_batch_run(
        batch_conn,  # type: ignore[arg-type]
        run_id=RUN_ID,
        resume_id=2,
        search_query=None,
        area_id=None,
        period_days=None,
        pages=None,
        per_page=None,
        max_responses=None,
        professional_roles=[],
        cover_letter_mode=None,
        live=False,
        started_at=NOW,
    )
    calls.extend(batch_conn.calls)

    decision_conn = FakeConnection((4,))
    await upsert_vacancy_decision(
        decision_conn,  # type: ignore[arg-type]
        run_id=RUN_ID,
        vacancy_id=3,
        stage="search_item_prefilter",
        accepted=False,
        reason="filtered",
        created_at=NOW,
    )
    calls.extend(decision_conn.calls)

    application_conn = FakeConnection((5,))
    await upsert_application(
        application_conn,  # type: ignore[arg-type]
        application_run_id=UUID("22222222-2222-4222-8222-222222222222"),
        vacancy_id=3,
        resume_id=2,
        submission_mode="negotiations_api",
        status="submitted",
        requested_at=NOW,
    )
    calls.extend(application_conn.calls)

    for query, _ in calls:
        match = re.search(
            r"INSERT INTO careerops\.(\w+)(?: AS \w+)? \((.*?)\)\s*VALUES",
            query,
            flags=re.DOTALL,
        )
        assert match is not None
        table = match.group(1)
        inserted = {column.strip() for column in match.group(2).split(",")}
        assert inserted <= table_columns[table]
