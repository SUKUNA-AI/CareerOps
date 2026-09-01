from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from careerops_contracts import CanonicalVacancy
from careerops_integrations.hh.models import HHVacancyOperational
from careerops_storage.postgres import (
    upsert_application,
    upsert_batch_run,
    upsert_partial_vacancy,
    upsert_resume,
    upsert_source_profile,
    upsert_vacancy,
    upsert_vacancy_decision,
)

NOW = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")


class FakeCursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class FakeConnection:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, params: tuple[Any, ...]) -> FakeCursor:
        self.calls.append((query, params))
        return FakeCursor(self.row)


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
    assert params == ("hh", "careerops-ml")

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
async def test_insert_columns_are_declared_by_core_migration() -> None:
    migration = Path("sql/migrations/0001_create_oltp_core.sql").read_text(
        encoding="utf-8"
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
