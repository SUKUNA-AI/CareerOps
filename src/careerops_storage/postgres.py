"""Asynchronous psycopg 3 persistence for the CareerOPS OLTP schema."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from careerops_contracts import CanonicalVacancy
from careerops_integrations.hh.models import HHVacancyOperational


@dataclass(frozen=True, slots=True)
class PostgresSettings:
    """PostgreSQL connection settings for the CareerOPS OLTP database."""

    dsn: str

    @classmethod
    def from_env(cls) -> PostgresSettings:
        """Load the PostgreSQL DSN without supplying an unsafe default."""

        dsn = os.getenv("CAREEROPS_POSTGRES_DSN")
        if not dsn:
            raise RuntimeError("CAREEROPS_POSTGRES_DSN is not set")
        return cls(dsn=dsn)


async def connect_postgres(settings: PostgresSettings) -> AsyncConnection[Any]:
    """Open a transactional psycopg async connection."""

    return await psycopg.AsyncConnection.connect(settings.dsn, autocommit=False)


def _returned_int(row: tuple[Any, ...] | None, entity: str) -> int:
    """Extract an integer id from an INSERT/UPSERT RETURNING row."""

    if row is None:
        raise RuntimeError(f"{entity} UPSERT returned no row")
    return int(row[0])


async def upsert_source_profile(
    conn: AsyncConnection[Any],
    *,
    source: str,
    profile_key: str,
) -> int:
    """Idempotently create or touch one external-source profile."""

    cursor = await conn.execute(
        """
        INSERT INTO careerops.source_profiles AS sp (source, profile_key)
        VALUES (%s, %s)
        ON CONFLICT (source, profile_key)
        DO UPDATE SET updated_at = now()
        RETURNING sp.id
        """,
        (source, profile_key),
    )
    row = await cursor.fetchone()
    return _returned_int(row, "source profile")


async def upsert_resume(
    conn: AsyncConnection[Any],
    *,
    source_profile_id: int,
    source_resume_id: str,
    title: str | None = None,
    raw_uri: str | None = None,
    content_hash: str | None = None,
    observed_at: datetime | None = None,
) -> int:
    """Upsert current resume state while preserving the observation interval."""

    cursor = await conn.execute(
        """
        INSERT INTO careerops.resumes AS r (
            source_profile_id,
            source_resume_id,
            title,
            raw_uri,
            content_hash,
            first_seen_at,
            last_seen_at
        )
        VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()), COALESCE(%s, now()))
        ON CONFLICT (source_profile_id, source_resume_id)
        DO UPDATE SET
            title = CASE
                WHEN EXCLUDED.last_seen_at >= r.last_seen_at
                THEN COALESCE(EXCLUDED.title, r.title)
                ELSE r.title
            END,
            raw_uri = CASE
                WHEN EXCLUDED.last_seen_at >= r.last_seen_at
                THEN COALESCE(EXCLUDED.raw_uri, r.raw_uri)
                ELSE r.raw_uri
            END,
            content_hash = CASE
                WHEN EXCLUDED.last_seen_at >= r.last_seen_at
                THEN COALESCE(EXCLUDED.content_hash, r.content_hash)
                ELSE r.content_hash
            END,
            first_seen_at = LEAST(r.first_seen_at, EXCLUDED.first_seen_at),
            last_seen_at = GREATEST(r.last_seen_at, EXCLUDED.last_seen_at),
            updated_at = now()
        RETURNING r.id
        """,
        (
            source_profile_id,
            source_resume_id,
            title,
            raw_uri,
            content_hash,
            observed_at,
            observed_at,
        ),
    )
    row = await cursor.fetchone()
    return _returned_int(row, "resume")


async def upsert_partial_vacancy(
    conn: AsyncConnection[Any],
    *,
    source: str,
    source_entity_id: str,
    observed_at: datetime,
    raw_uri: str,
    content_hash: str,
    source_employer_id: str | None = None,
    title: str | None = None,
    company_name: str | None = None,
    location: str | None = None,
    source_url: str | None = None,
    published_at: datetime | None = None,
) -> int:
    """Persist only fields proven by an HH search item."""

    cursor = await conn.execute(
        """
        INSERT INTO careerops.vacancies AS v (
            source,
            source_entity_id,
            source_employer_id,
            title,
            company_name,
            location,
            source_url,
            published_at,
            first_seen_at,
            last_seen_at,
            latest_raw_uri,
            latest_content_hash
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, source_entity_id)
        DO UPDATE SET
            source_employer_id = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN COALESCE(EXCLUDED.source_employer_id, v.source_employer_id)
                ELSE v.source_employer_id
            END,
            title = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN COALESCE(EXCLUDED.title, v.title)
                ELSE v.title
            END,
            company_name = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN COALESCE(EXCLUDED.company_name, v.company_name)
                ELSE v.company_name
            END,
            location = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN COALESCE(EXCLUDED.location, v.location)
                ELSE v.location
            END,
            source_url = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN COALESCE(EXCLUDED.source_url, v.source_url)
                ELSE v.source_url
            END,
            published_at = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN COALESCE(EXCLUDED.published_at, v.published_at)
                ELSE v.published_at
            END,
            first_seen_at = LEAST(v.first_seen_at, EXCLUDED.first_seen_at),
            last_seen_at = GREATEST(v.last_seen_at, EXCLUDED.last_seen_at),
            latest_raw_uri = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.latest_raw_uri
                ELSE v.latest_raw_uri
            END,
            latest_content_hash = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.latest_content_hash
                ELSE v.latest_content_hash
            END,
            updated_at = now()
        RETURNING v.id
        """,
        (
            source,
            source_entity_id,
            source_employer_id,
            title,
            company_name,
            location,
            source_url,
            published_at,
            observed_at,
            observed_at,
            raw_uri,
            content_hash,
        ),
    )
    row = await cursor.fetchone()
    return _returned_int(row, "partial vacancy")


async def upsert_vacancy(
    conn: AsyncConnection[Any],
    *,
    vacancy: CanonicalVacancy,
    operational: HHVacancyOperational,
    source_employer_id: str | None = None,
) -> int:
    """Upsert a fully mapped vacancy and HH-only operational state."""

    if operational.vacancy_id != vacancy.source_entity_id:
        raise ValueError(
            "canonical and operational vacancy IDs differ: "
            f"{vacancy.source_entity_id!r} != {operational.vacancy_id!r}"
        )

    cursor = await conn.execute(
        """
        INSERT INTO careerops.vacancies AS v (
            source,
            source_entity_id,
            source_employer_id,
            title,
            company_name,
            description,
            salary_from,
            salary_to,
            salary_currency,
            location,
            remote,
            employment_type,
            experience,
            source_url,
            relations,
            archived,
            closed_for_applicants,
            has_test,
            response_letter_required,
            response_url,
            published_at,
            first_seen_at,
            last_seen_at,
            latest_raw_uri,
            latest_content_hash
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (source, source_entity_id)
        DO UPDATE SET
            source_employer_id = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.source_employer_id
                ELSE v.source_employer_id
            END,
            title = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at THEN EXCLUDED.title ELSE v.title
            END,
            company_name = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.company_name
                ELSE v.company_name
            END,
            description = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.description
                ELSE v.description
            END,
            salary_from = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.salary_from
                ELSE v.salary_from
            END,
            salary_to = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.salary_to
                ELSE v.salary_to
            END,
            salary_currency = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.salary_currency
                ELSE v.salary_currency
            END,
            location = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.location
                ELSE v.location
            END,
            remote = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at THEN EXCLUDED.remote ELSE v.remote
            END,
            employment_type = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.employment_type
                ELSE v.employment_type
            END,
            experience = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.experience
                ELSE v.experience
            END,
            source_url = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.source_url
                ELSE v.source_url
            END,
            relations = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.relations
                ELSE v.relations
            END,
            archived = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.archived
                ELSE v.archived
            END,
            closed_for_applicants = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.closed_for_applicants
                ELSE v.closed_for_applicants
            END,
            has_test = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.has_test
                ELSE v.has_test
            END,
            response_letter_required = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.response_letter_required
                ELSE v.response_letter_required
            END,
            response_url = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.response_url
                ELSE v.response_url
            END,
            published_at = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.published_at
                ELSE v.published_at
            END,
            first_seen_at = LEAST(v.first_seen_at, EXCLUDED.first_seen_at),
            last_seen_at = GREATEST(v.last_seen_at, EXCLUDED.last_seen_at),
            latest_raw_uri = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.latest_raw_uri
                ELSE v.latest_raw_uri
            END,
            latest_content_hash = CASE
                WHEN EXCLUDED.last_seen_at >= v.last_seen_at
                THEN EXCLUDED.latest_content_hash
                ELSE v.latest_content_hash
            END,
            updated_at = now()
        RETURNING v.id
        """,
        (
            vacancy.source,
            vacancy.source_entity_id,
            source_employer_id,
            vacancy.title,
            vacancy.company_name,
            vacancy.description,
            vacancy.salary_from,
            vacancy.salary_to,
            vacancy.salary_currency,
            vacancy.location,
            vacancy.remote,
            vacancy.employment_type,
            vacancy.experience,
            vacancy.source_url,
            list(operational.relations),
            operational.archived,
            operational.closed_for_applicants,
            operational.has_test,
            operational.response_letter_required,
            operational.response_url,
            vacancy.published_at,
            vacancy.collected_at,
            vacancy.collected_at,
            vacancy.raw_uri,
            vacancy.content_hash,
        ),
    )
    row = await cursor.fetchone()
    return _returned_int(row, "vacancy")


async def upsert_batch_run(
    conn: AsyncConnection[Any],
    *,
    run_id: UUID,
    resume_id: int,
    search_query: str | None,
    area_id: str | None,
    period_days: int | None,
    pages: int | None,
    per_page: int | None,
    max_responses: int | None,
    professional_roles: Sequence[str],
    cover_letter_mode: str | None,
    live: bool,
    started_at: datetime,
    finished_at: datetime | None = None,
    status: str = "incomplete",
    discovered: int | None = None,
    prefiltered: int | None = None,
    full_fetched: int | None = None,
    accepted: int | None = None,
    submitted: int | None = None,
    confirmed: int | None = None,
    failed: int | None = None,
    stopped_on_captcha: bool | None = None,
    s3_prefix: str | None = None,
) -> UUID:
    """Create or finalize one batch without regressing a finished run."""

    cursor = await conn.execute(
        """
        INSERT INTO careerops.batch_runs AS br (
            id,
            resume_id,
            search_query,
            area_id,
            period_days,
            pages,
            per_page,
            max_responses,
            professional_roles,
            cover_letter_mode,
            live,
            status,
            discovered,
            prefiltered,
            full_fetched,
            accepted,
            submitted,
            confirmed,
            failed,
            stopped_on_captcha,
            started_at,
            finished_at,
            s3_prefix
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (id)
        DO UPDATE SET
            resume_id = EXCLUDED.resume_id,
            search_query = EXCLUDED.search_query,
            area_id = EXCLUDED.area_id,
            period_days = EXCLUDED.period_days,
            pages = EXCLUDED.pages,
            per_page = EXCLUDED.per_page,
            max_responses = EXCLUDED.max_responses,
            professional_roles = EXCLUDED.professional_roles,
            cover_letter_mode = EXCLUDED.cover_letter_mode,
            live = EXCLUDED.live,
            status = CASE
                WHEN br.status = 'finished' AND EXCLUDED.status = 'incomplete'
                THEN br.status
                ELSE EXCLUDED.status
            END,
            discovered = COALESCE(EXCLUDED.discovered, br.discovered),
            prefiltered = COALESCE(EXCLUDED.prefiltered, br.prefiltered),
            full_fetched = COALESCE(EXCLUDED.full_fetched, br.full_fetched),
            accepted = COALESCE(EXCLUDED.accepted, br.accepted),
            submitted = COALESCE(EXCLUDED.submitted, br.submitted),
            confirmed = COALESCE(EXCLUDED.confirmed, br.confirmed),
            failed = COALESCE(EXCLUDED.failed, br.failed),
            stopped_on_captcha = COALESCE(
                EXCLUDED.stopped_on_captcha,
                br.stopped_on_captcha
            ),
            started_at = LEAST(br.started_at, EXCLUDED.started_at),
            finished_at = COALESCE(EXCLUDED.finished_at, br.finished_at),
            s3_prefix = COALESCE(EXCLUDED.s3_prefix, br.s3_prefix),
            updated_at = now()
        RETURNING br.id
        """,
        (
            run_id,
            resume_id,
            search_query,
            area_id,
            period_days,
            pages,
            per_page,
            max_responses,
            list(professional_roles),
            cover_letter_mode,
            live,
            status,
            discovered,
            prefiltered,
            full_fetched,
            accepted,
            submitted,
            confirmed,
            failed,
            stopped_on_captcha,
            started_at,
            finished_at,
            s3_prefix,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("batch run UPSERT returned no row")
    return UUID(str(row[0]))


async def upsert_vacancy_decision(
    conn: AsyncConnection[Any],
    *,
    run_id: UUID,
    vacancy_id: int,
    stage: str,
    accepted: bool,
    reason: str,
    matched_domains: Sequence[str] = (),
    blocked_terms: Sequence[str] = (),
    metadata: dict[str, Any] | None = None,
    created_at: datetime,
) -> int:
    """Upsert the current normalized decision for one batch stage."""

    cursor = await conn.execute(
        """
        INSERT INTO careerops.vacancy_decisions AS vd (
            run_id,
            vacancy_id,
            stage,
            accepted,
            reason,
            matched_domains,
            blocked_terms,
            metadata,
            created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id, vacancy_id, stage)
        DO UPDATE SET
            accepted = EXCLUDED.accepted,
            reason = EXCLUDED.reason,
            matched_domains = EXCLUDED.matched_domains,
            blocked_terms = EXCLUDED.blocked_terms,
            metadata = EXCLUDED.metadata,
            created_at = EXCLUDED.created_at
        RETURNING vd.id
        """,
        (
            run_id,
            vacancy_id,
            stage,
            accepted,
            reason,
            list(matched_domains),
            list(blocked_terms),
            Jsonb(metadata or {}),
            created_at,
        ),
    )
    row = await cursor.fetchone()
    return _returned_int(row, "vacancy decision")


async def upsert_application(
    conn: AsyncConnection[Any],
    *,
    application_run_id: UUID,
    vacancy_id: int,
    resume_id: int,
    submission_mode: str,
    status: str,
    requested_at: datetime,
    batch_run_id: UUID | None = None,
    confirmed: bool | None = None,
    finished_at: datetime | None = None,
    cover_letter_uri: str | None = None,
    request_uri: str | None = None,
    result_uri: str | None = None,
    before_uri: str | None = None,
    after_uri: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    upstream_metadata: dict[str, Any] | None = None,
) -> int:
    """Upsert the latest provable application state for a resume and vacancy."""

    cursor = await conn.execute(
        """
        INSERT INTO careerops.applications AS a (
            application_run_id,
            batch_run_id,
            vacancy_id,
            resume_id,
            submission_mode,
            status,
            confirmed,
            requested_at,
            finished_at,
            cover_letter_uri,
            request_uri,
            result_uri,
            before_uri,
            after_uri,
            error_type,
            error_message,
            upstream_metadata
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (vacancy_id, resume_id)
        DO UPDATE SET
            application_run_id = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.application_run_id
                ELSE a.application_run_id
            END,
            batch_run_id = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.batch_run_id
                ELSE a.batch_run_id
            END,
            submission_mode = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.submission_mode
                ELSE a.submission_mode
            END,
            status = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.status
                ELSE a.status
            END,
            confirmed = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.confirmed
                ELSE a.confirmed
            END,
            requested_at = GREATEST(a.requested_at, EXCLUDED.requested_at),
            finished_at = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.finished_at
                ELSE a.finished_at
            END,
            cover_letter_uri = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.cover_letter_uri
                ELSE a.cover_letter_uri
            END,
            request_uri = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.request_uri
                ELSE a.request_uri
            END,
            result_uri = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.result_uri
                ELSE a.result_uri
            END,
            before_uri = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.before_uri
                ELSE a.before_uri
            END,
            after_uri = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.after_uri
                ELSE a.after_uri
            END,
            error_type = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.error_type
                ELSE a.error_type
            END,
            error_message = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.error_message
                ELSE a.error_message
            END,
            upstream_metadata = CASE
                WHEN EXCLUDED.requested_at >= a.requested_at
                THEN EXCLUDED.upstream_metadata
                ELSE a.upstream_metadata
            END,
            updated_at = now()
        RETURNING a.id
        """,
        (
            application_run_id,
            batch_run_id,
            vacancy_id,
            resume_id,
            submission_mode,
            status,
            confirmed,
            requested_at,
            finished_at,
            cover_letter_uri,
            request_uri,
            result_uri,
            before_uri,
            after_uri,
            error_type,
            error_message,
            Jsonb(upstream_metadata or {}),
        ),
    )
    row = await cursor.fetchone()
    return _returned_int(row, "application")


class PostgresOLTPStore:
    """Async object-oriented facade over CareerOPS PostgreSQL UPSERT functions."""

    def __init__(self, conn: AsyncConnection[Any]) -> None:
        """Bind all persistence operations to one transaction connection."""

        self.conn = conn

    async def upsert_source_profile(self, **kwargs: Any) -> int:
        """Persist a source profile through the bound connection."""

        return await upsert_source_profile(self.conn, **kwargs)

    async def upsert_resume(self, **kwargs: Any) -> int:
        """Persist a resume through the bound connection."""

        return await upsert_resume(self.conn, **kwargs)

    async def upsert_partial_vacancy(self, **kwargs: Any) -> int:
        """Persist search-item-supported vacancy fields."""

        return await upsert_partial_vacancy(self.conn, **kwargs)

    async def upsert_vacancy(self, **kwargs: Any) -> int:
        """Persist the fully mapped current vacancy state."""

        return await upsert_vacancy(self.conn, **kwargs)

    async def upsert_batch_run(self, **kwargs: Any) -> UUID:
        """Persist or finalize one batch run."""

        return await upsert_batch_run(self.conn, **kwargs)

    async def upsert_vacancy_decision(self, **kwargs: Any) -> int:
        """Persist one normalized vacancy decision."""

        return await upsert_vacancy_decision(self.conn, **kwargs)

    async def upsert_application(self, **kwargs: Any) -> int:
        """Persist one completed and provable application audit."""

        return await upsert_application(self.conn, **kwargs)
