"""Asynchronous psycopg 3 persistence for the CareerOPS OLTP schema."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from careerops_contracts import CanonicalVacancy, RawVacancyRef
from careerops_integrations.hh.application_claims import (
    ApplicationClaimAcquisition,
    ApplicationClaimIdentityNotMaterialized,
    ApplicationClaimRecord,
    ApplicationClaimStatus,
    ApplicationClaimTransitionError,
    ApplicationIdentity,
)
from careerops_integrations.hh.mapper import extract_operational, map_hh_vacancy
from careerops_integrations.hh.models import HHVacancyOperational
from careerops_integrations.hh.observe import ObserveQueryCursorReservation
from careerops_integrations.hh.resume_sync import (
    AccountResumeInventory,
    ReconciledResume,
    ResumeLifecycle,
)
from careerops_integrations.hh.search_queries import SearchQueryDefinition


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


async def connect_postgres(
    settings: PostgresSettings,
    *,
    autocommit: bool = False,
) -> AsyncConnection[Any]:
    """Open a transactional psycopg async connection."""

    return await psycopg.AsyncConnection.connect(settings.dsn, autocommit=autocommit)


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
    account_key: str | None = None,
) -> int:
    """Idempotently create or touch one external-source profile."""

    cursor = await conn.execute(
        """
        INSERT INTO careerops.source_profiles AS sp (source, profile_key, account_key)
        VALUES (%s, %s, %s)
        ON CONFLICT (source, profile_key)
        DO UPDATE SET
            account_key = COALESCE(EXCLUDED.account_key, sp.account_key),
            updated_at = now()
        RETURNING sp.id
        """,
        (source, profile_key, account_key),
    )
    row = await cursor.fetchone()
    return _returned_int(row, "source profile")

async def activate_search_query_state(
    conn: AsyncConnection[Any],
    *,
    source_profile: str,
    account_key: str,
    definition: SearchQueryDefinition,
    activated_at: datetime,
) -> int:
    """Return the active durable version of one logical HH search query."""

    normalized_account_key = account_key.strip()
    if not normalized_account_key:
        raise ValueError("account_key must not be empty")

    if not definition.query_key.strip():
        raise ValueError("query_key must not be empty")

    signature = definition.query_signature
    if len(signature) != 64 or any(
        char not in "0123456789abcdef"
        for char in signature
    ):
        raise ValueError(
            "query_signature must be a lowercase SHA-256 hex digest"
        )

    async with conn.transaction():
        source_profile_id = await upsert_source_profile(
            conn,
            source="hh",
            profile_key=source_profile,
            account_key=normalized_account_key,
        )

        # Serialize query-version changes for one HH source profile.
        lock_cursor = await conn.execute(
            """
            SELECT id
            FROM careerops.source_profiles
            WHERE id = %s
            FOR UPDATE
            """,
            (source_profile_id,),
        )

        if await lock_cursor.fetchone() is None:
            raise RuntimeError(
                "source profile disappeared while activating search query state"
            )

        existing_cursor = await conn.execute(
            """
            SELECT
                id,
                query_set_key,
                request_params
            FROM careerops.search_query_states
            WHERE source_profile_id = %s
              AND query_key = %s
              AND query_signature = %s
            FOR UPDATE
            """,
            (
                source_profile_id,
                definition.query_key,
                signature,
            ),
        )

        existing = await existing_cursor.fetchone()

        if existing is not None:
            existing_query_set = str(existing[1])
            existing_request_params = existing[2]

            if (
                existing_query_set != definition.query_set_key
                or existing_request_params != definition.request_params
            ):
                raise RuntimeError(
                    "search query signature collision or "
                    "canonicalization mismatch"
                )

        # A different active version becomes historical.
        await conn.execute(
            """
            UPDATE careerops.search_query_states
            SET
                is_active = false,
                retired_at = %s,
                updated_at = now()
            WHERE source_profile_id = %s
              AND query_key = %s
              AND is_active
              AND query_signature <> %s
            """,
            (
                activated_at,
                source_profile_id,
                definition.query_key,
                signature,
            ),
        )

        if existing is not None:
            state_id = int(existing[0])

            cursor = await conn.execute(
                """
                UPDATE careerops.search_query_states
                SET
                    account_key = %s,
                    is_active = true,
                    retired_at = NULL,
                    updated_at = now()
                WHERE id = %s
                RETURNING id
                """,
                (
                    normalized_account_key,
                    state_id,
                ),
            )

            row = await cursor.fetchone()

            if row is None:
                raise RuntimeError(
                    "search query state reactivation returned no row"
                )

            return int(row[0])

        cursor = await conn.execute(
            """
            INSERT INTO careerops.search_query_states (
                source_profile_id,
                account_key,
                query_key,
                query_set_key,
                query_signature,
                request_params,
                is_active,
                retired_at
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                true,
                NULL
            )
            RETURNING id
            """,
            (
                source_profile_id,
                normalized_account_key,
                definition.query_key,
                definition.query_set_key,
                signature,
                Jsonb(definition.request_params),
            ),
        )

        row = await cursor.fetchone()

        if row is None:
            raise RuntimeError(
                "search query state INSERT returned no row"
            )

        return int(row[0])

class PostgresSearchQueryStateStore:
    """PostgreSQL persistence for versioned lossless HH search queries."""

    def __init__(self, conn: AsyncConnection[Any]) -> None:
        self._conn = conn

    async def activate(
        self,
        *,
        source_profile: str,
        account_key: str,
        definition: SearchQueryDefinition,
        activated_at: datetime,
    ) -> int:
        """Return the durable active state id for one query definition."""

        return await activate_search_query_state(
            self._conn,
            source_profile=source_profile,
            account_key=account_key,
            definition=definition,
            activated_at=activated_at,
        )


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


async def upsert_reconciled_resume(
    conn: AsyncConnection[Any],
    *,
    source_profile_id: int,
    resume: ReconciledResume,
) -> int:
    """Persist the complete authoritative runtime lifecycle for one HH resume."""

    cursor = await conn.execute(
        """
        INSERT INTO careerops.resumes AS r (
            source_profile_id,
            source_resume_id,
            title,
            content_hash,
            first_seen_at,
            last_seen_at,
            upstream_status,
            lifecycle,
            present_in_upstream,
            inactive_at,
            binding_key,
            binding_version,
            target_key,
            binding_enabled,
            auto_apply,
            selectable_for_evaluation,
            selectable_for_auto_apply,
            query_sets,
            source_payload
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (source_profile_id, source_resume_id)
        DO UPDATE SET
            title = EXCLUDED.title,
            content_hash = EXCLUDED.content_hash,
            first_seen_at = LEAST(r.first_seen_at, EXCLUDED.first_seen_at),
            last_seen_at = GREATEST(r.last_seen_at, EXCLUDED.last_seen_at),
            upstream_status = EXCLUDED.upstream_status,
            lifecycle = EXCLUDED.lifecycle,
            present_in_upstream = EXCLUDED.present_in_upstream,
            inactive_at = EXCLUDED.inactive_at,
            binding_key = EXCLUDED.binding_key,
            binding_version = EXCLUDED.binding_version,
            target_key = EXCLUDED.target_key,
            binding_enabled = EXCLUDED.binding_enabled,
            auto_apply = EXCLUDED.auto_apply,
            selectable_for_evaluation = EXCLUDED.selectable_for_evaluation,
            selectable_for_auto_apply = EXCLUDED.selectable_for_auto_apply,
            query_sets = EXCLUDED.query_sets,
            source_payload = EXCLUDED.source_payload,
            updated_at = now()
        RETURNING r.id
        """,
        (
            source_profile_id,
            resume.source_resume_id,
            resume.current_title,
            resume.content_sha256,
            resume.first_seen_at,
            resume.last_seen_at,
            resume.upstream_status,
            resume.lifecycle.value,
            resume.lifecycle is ResumeLifecycle.ACTIVE,
            resume.inactive_at,
            resume.binding_key,
            resume.binding_version,
            resume.target_key,
            resume.binding_enabled,
            resume.auto_apply,
            resume.selectable_for_evaluation,
            resume.selectable_for_auto_apply,
            list(resume.query_sets),
            Jsonb(resume.source_payload),
        ),
    )
    row = await cursor.fetchone()
    return _returned_int(row, "reconciled resume")


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


def _claim_record(
    row: tuple[Any, ...] | None,
    *,
    identity: ApplicationIdentity,
) -> ApplicationClaimRecord:
    if row is None:
        raise RuntimeError("application claim operation returned no row")
    return ApplicationClaimRecord(
        identity=identity,
        account_key=str(row[0]),
        application_run_id=UUID(str(row[1])),
        status=ApplicationClaimStatus(str(row[2])),
        attempt_count=int(row[3]),
        claimed_at=row[4],
        state_changed_at=row[5],
    )


async def _resolve_application_identity_ids(
    conn: AsyncConnection[Any],
    identity: ApplicationIdentity,
) -> tuple[int, int]:
    """Resolve a stable HH natural key to the canonical OLTP entity ids."""

    cursor = await conn.execute(
        """
        SELECT r.id, v.id
        FROM careerops.source_profiles AS sp
        JOIN careerops.resumes AS r ON r.source_profile_id = sp.id
        JOIN careerops.vacancies AS v
          ON v.source = 'hh'
         AND v.source_entity_id = %s
        WHERE sp.source = 'hh'
          AND sp.profile_key = %s
          AND r.source_resume_id = %s
        """,
        (
            identity.vacancy_id,
            identity.source_profile,
            identity.source_resume_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ApplicationClaimIdentityNotMaterialized(
            "application claim identity is not materialized in PostgreSQL: "
            f"profile={identity.source_profile!r}, "
            f"resume={identity.source_resume_id!r}, "
            f"vacancy={identity.vacancy_id!r}"
        )
    return int(row[0]), int(row[1])


async def prepare_application_claim_identity(
    conn: AsyncConnection[Any],
    *,
    identity: ApplicationIdentity,
    account_key: str,
    vacancy: dict[str, Any],
    observed_at: datetime,
    raw_uri: str,
    content_hash: str,
) -> None:
    """Materialize an APPLY vacancy while requiring an existing reconciled resume.

    The caller owns the short PostgreSQL transaction. The authoritative HH
    vacancy payload is already fetched and durably stored before this function;
    no external network work is performed here.
    """

    normalized_account_key = account_key.strip()
    if not normalized_account_key:
        raise ValueError("account_key must not be empty")
    payload_vacancy_id = str(vacancy.get("id", "")).strip()
    if payload_vacancy_id != identity.vacancy_id:
        raise ValueError(
            "application vacancy payload identity mismatch: "
            f"expected={identity.vacancy_id!r}, actual={payload_vacancy_id!r}"
        )

    cursor = await conn.execute(
        """
        SELECT r.id
        FROM careerops.source_profiles AS sp
        JOIN careerops.resumes AS r ON r.source_profile_id = sp.id
        WHERE sp.source = 'hh'
          AND sp.profile_key = %s
          AND r.source_resume_id = %s
          AND r.lifecycle = 'active'
          AND r.present_in_upstream
        """,
        (identity.source_profile, identity.source_resume_id),
    )
    if await cursor.fetchone() is None:
        raise ApplicationClaimIdentityNotMaterialized(
            "application resume identity is not current in PostgreSQL: "
            f"profile={identity.source_profile!r}, "
            f"resume={identity.source_resume_id!r}, "
            f"account_key={normalized_account_key!r}"
        )

    raw = RawVacancyRef(
        source="hh",
        source_entity_id=identity.vacancy_id,
        raw_uri=raw_uri,
        content_hash=content_hash,
        collected_at=observed_at,
    )
    employer = vacancy.get("employer") or {}
    employer_id = str(employer.get("id", "")).strip() or None
    await upsert_vacancy(
        conn,
        vacancy=map_hh_vacancy(vacancy, raw=raw),
        operational=extract_operational(vacancy),
        source_employer_id=employer_id,
    )


async def acquire_application_claim(
    conn: AsyncConnection[Any],
    *,
    identity: ApplicationIdentity,
    account_key: str,
    application_run_id: UUID,
    claimed_at: datetime,
) -> ApplicationClaimAcquisition:
    """Atomically acquire a canonical resume/vacancy claim.

    ``account_key`` is stored only as mutable provenance and is deliberately not
    part of either identity resolution or the PostgreSQL conflict target.
    """

    normalized_account_key = account_key.strip()
    if not normalized_account_key:
        raise ValueError("account_key must not be empty")
    resume_db_id, vacancy_db_id = await _resolve_application_identity_ids(
        conn,
        identity,
    )

    cursor = await conn.execute(
        """
        INSERT INTO careerops.application_claims AS ac (
            id,
            account_key,
            resume_id,
            vacancy_id,
            application_run_id,
            status,
            attempt_count,
            claimed_at,
            state_changed_at
        )
        VALUES (%s, %s, %s, %s, %s, 'CLAIMED', 1, %s, %s)
        ON CONFLICT (resume_id, vacancy_id)
        DO UPDATE SET
            id = EXCLUDED.id,
            account_key = EXCLUDED.account_key,
            application_run_id = EXCLUDED.application_run_id,
            status = 'CLAIMED',
            attempt_count = ac.attempt_count + 1,
            claimed_at = EXCLUDED.claimed_at,
            state_changed_at = EXCLUDED.state_changed_at,
            submitted_at = NULL,
            finished_at = NULL,
            last_error_type = NULL,
            last_error_message = NULL,
            upstream_evidence = '{}'::jsonb,
            updated_at = now()
        WHERE ac.status = 'FAILED_SAFE_TO_RETRY'
        RETURNING
            ac.account_key,
            ac.application_run_id,
            ac.status,
            ac.attempt_count,
            ac.claimed_at,
            ac.state_changed_at
        """,
        (
            uuid4(),
            normalized_account_key,
            resume_db_id,
            vacancy_db_id,
            application_run_id,
            claimed_at,
            claimed_at,
        ),
    )
    row = await cursor.fetchone()
    if row is not None:
        return ApplicationClaimAcquisition(
            acquired=True,
            record=_claim_record(row, identity=identity),
        )

    cursor = await conn.execute(
        """
        SELECT
            account_key,
            application_run_id,
            status,
            attempt_count,
            claimed_at,
            state_changed_at
        FROM careerops.application_claims
        WHERE resume_id = %s
          AND vacancy_id = %s
        """,
        (resume_db_id, vacancy_db_id),
    )
    return ApplicationClaimAcquisition(
        acquired=False,
        record=_claim_record(await cursor.fetchone(), identity=identity),
    )


async def transition_application_claim(
    conn: AsyncConnection[Any],
    *,
    identity: ApplicationIdentity,
    application_run_id: UUID,
    expected: tuple[ApplicationClaimStatus, ...],
    status: ApplicationClaimStatus,
    changed_at: datetime,
    error_type: str | None = None,
    error_message: str | None = None,
    upstream_evidence: dict[str, Any] | None = None,
) -> ApplicationClaimRecord:
    """Transition only the current owner from an explicitly allowed state."""

    if not expected:
        raise ValueError("expected claim states must not be empty")
    resume_db_id, vacancy_db_id = await _resolve_application_identity_ids(
        conn,
        identity,
    )
    terminal = status in {
        ApplicationClaimStatus.SUBMITTED,
        ApplicationClaimStatus.UNCERTAIN,
        ApplicationClaimStatus.FAILED_SAFE_TO_RETRY,
    }
    cursor = await conn.execute(
        """
        UPDATE careerops.application_claims AS ac
        SET
            status = %s,
            state_changed_at = %s,
            submitted_at = CASE
                WHEN %s = 'SUBMITTED' THEN %s
                ELSE ac.submitted_at
            END,
            finished_at = CASE
                WHEN %s THEN %s
                ELSE NULL
            END,
            last_error_type = %s,
            last_error_message = %s,
            upstream_evidence = %s,
            updated_at = now()
        WHERE ac.resume_id = %s
          AND ac.vacancy_id = %s
          AND ac.application_run_id = %s
          AND ac.status = ANY(%s)
        RETURNING
            ac.account_key,
            ac.application_run_id,
            ac.status,
            ac.attempt_count,
            ac.claimed_at,
            ac.state_changed_at
        """,
        (
            status.value,
            changed_at,
            status.value,
            changed_at,
            terminal,
            changed_at,
            error_type,
            error_message,
            Jsonb(upstream_evidence or {}),
            resume_db_id,
            vacancy_db_id,
            application_run_id,
            [item.value for item in expected],
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ApplicationClaimTransitionError(
            "application claim state changed concurrently or is owned by another run"
        )
    return _claim_record(row, identity=identity)


async def reserve_observe_query_window(
    conn: AsyncConnection[Any],
    *,
    source_profile: str,
    account_key: str,
    catalog_signature: str,
    catalog_size: int,
    max_queries: int,
    run_id: UUID,
    reserved_at: datetime,
) -> ObserveQueryCursorReservation:
    """Atomically reserve and advance one source profile's circular query window."""

    if catalog_size < 1:
        raise ValueError("catalog_size must be >= 1")
    if max_queries < 1:
        raise ValueError("max_queries must be >= 1")
    if len(catalog_signature) != 64:
        raise ValueError("catalog_signature must be a SHA-256 hex digest")
    window_size = min(catalog_size, max_queries)

    async with conn.transaction():
        source_profile_id = await upsert_source_profile(
            conn,
            source="hh",
            profile_key=source_profile,
            account_key=account_key,
        )
        cursor = await conn.execute(
            """
            INSERT INTO careerops.observe_query_cursors AS oqc (
                source_profile_id,
                account_key,
                catalog_signature,
                catalog_size,
                next_query_offset,
                last_window_start,
                last_window_size,
                last_run_id,
                last_reserved_at
            )
            VALUES (%s, %s, %s, %s, %s, 0, %s, %s, %s)
            ON CONFLICT (source_profile_id)
            DO UPDATE SET
                account_key = EXCLUDED.account_key,
                last_window_start = CASE
                    WHEN oqc.catalog_signature = EXCLUDED.catalog_signature
                     AND oqc.catalog_size = EXCLUDED.catalog_size
                    THEN oqc.next_query_offset
                    ELSE 0
                END,
                last_window_size = EXCLUDED.last_window_size,
                next_query_offset = CASE
                    WHEN oqc.catalog_signature = EXCLUDED.catalog_signature
                     AND oqc.catalog_size = EXCLUDED.catalog_size
                    THEN (
                        oqc.next_query_offset + EXCLUDED.last_window_size
                    ) %% EXCLUDED.catalog_size
                    ELSE EXCLUDED.next_query_offset
                END,
                catalog_signature = EXCLUDED.catalog_signature,
                catalog_size = EXCLUDED.catalog_size,
                last_run_id = EXCLUDED.last_run_id,
                last_reserved_at = EXCLUDED.last_reserved_at,
                updated_at = now()
            RETURNING
                oqc.catalog_signature,
                oqc.catalog_size,
                oqc.last_window_start,
                oqc.last_window_size,
                oqc.next_query_offset
            """,
            (
                source_profile_id,
                account_key,
                catalog_signature,
                catalog_size,
                window_size % catalog_size,
                window_size,
                run_id,
                reserved_at,
            ),
        )
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("OBSERVE query cursor reservation returned no row")
    return ObserveQueryCursorReservation(
        source_profile=source_profile,
        account_key=account_key,
        catalog_signature=str(row[0]),
        catalog_size=int(row[1]),
        window_start=int(row[2]),
        window_size=int(row[3]),
        next_query_offset=int(row[4]),
    )


async def upsert_observation_run(
    conn: AsyncConnection[Any],
    *,
    run_id: UUID,
    source_profile_id: int,
    account_key: str,
    status: str,
    query_set_keys: Sequence[str],
    query_keys: Sequence[str],
    query_catalog_size: int,
    query_catalog_signature: str,
    max_queries_per_run: int,
    query_cursor_start: int,
    query_cursor_next: int,
    query_rotation_wrapped: bool,
    pages: int,
    per_page: int,
    max_unique_vacancies: int,
    max_full_fetches: int,
    search_delay_seconds: float,
    full_fetch_min_delay_seconds: float,
    full_fetch_max_delay_seconds: float,
    started_at: datetime,
    s3_prefix: str,
    finished_at: datetime | None = None,
    search_observation_count: int | None = None,
    unique_vacancy_count: int | None = None,
    candidate_count: int | None = None,
    full_fetch_attempted: int | None = None,
    full_fetched: int | None = None,
    evaluation_candidate_count: int | None = None,
    failed: int | None = None,
    stopped_on_captcha: bool | None = None,
) -> UUID:
    """Persist an account-wide OBSERVE run without inventing a single resume."""

    cursor = await conn.execute(
        """
        INSERT INTO careerops.observation_runs AS obr (
            id, source_profile_id, account_key, status, query_set_keys, query_keys,
            query_catalog_size, query_catalog_signature, max_queries_per_run,
            query_cursor_start, query_cursor_next, query_rotation_wrapped,
            pages, per_page, max_unique_vacancies, max_full_fetches,
            search_delay_seconds, full_fetch_min_delay_seconds,
            full_fetch_max_delay_seconds, search_observation_count,
            unique_vacancy_count, candidate_count, full_fetch_attempted,
            full_fetched, evaluation_candidate_count, failed, stopped_on_captcha,
            started_at, finished_at, s3_prefix
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (id)
        DO UPDATE SET
            source_profile_id = EXCLUDED.source_profile_id,
            account_key = EXCLUDED.account_key,
            status = EXCLUDED.status,
            query_set_keys = EXCLUDED.query_set_keys,
            query_keys = EXCLUDED.query_keys,
            query_catalog_size = EXCLUDED.query_catalog_size,
            query_catalog_signature = EXCLUDED.query_catalog_signature,
            max_queries_per_run = EXCLUDED.max_queries_per_run,
            query_cursor_start = EXCLUDED.query_cursor_start,
            query_cursor_next = EXCLUDED.query_cursor_next,
            query_rotation_wrapped = EXCLUDED.query_rotation_wrapped,
            pages = EXCLUDED.pages,
            per_page = EXCLUDED.per_page,
            max_unique_vacancies = EXCLUDED.max_unique_vacancies,
            max_full_fetches = EXCLUDED.max_full_fetches,
            search_delay_seconds = EXCLUDED.search_delay_seconds,
            full_fetch_min_delay_seconds = EXCLUDED.full_fetch_min_delay_seconds,
            full_fetch_max_delay_seconds = EXCLUDED.full_fetch_max_delay_seconds,
            search_observation_count = COALESCE(
                EXCLUDED.search_observation_count,
                obr.search_observation_count
            ),
            unique_vacancy_count = COALESCE(
                EXCLUDED.unique_vacancy_count,
                obr.unique_vacancy_count
            ),
            candidate_count = COALESCE(EXCLUDED.candidate_count, obr.candidate_count),
            full_fetch_attempted = COALESCE(
                EXCLUDED.full_fetch_attempted,
                obr.full_fetch_attempted
            ),
            full_fetched = COALESCE(EXCLUDED.full_fetched, obr.full_fetched),
            evaluation_candidate_count = COALESCE(
                EXCLUDED.evaluation_candidate_count,
                obr.evaluation_candidate_count
            ),
            failed = COALESCE(EXCLUDED.failed, obr.failed),
            stopped_on_captcha = COALESCE(
                EXCLUDED.stopped_on_captcha,
                obr.stopped_on_captcha
            ),
            started_at = LEAST(obr.started_at, EXCLUDED.started_at),
            finished_at = COALESCE(EXCLUDED.finished_at, obr.finished_at),
            s3_prefix = EXCLUDED.s3_prefix,
            updated_at = now()
        RETURNING obr.id
        """,
        (
            run_id,
            source_profile_id,
            account_key,
            status,
            list(query_set_keys),
            list(query_keys),
            query_catalog_size,
            query_catalog_signature,
            max_queries_per_run,
            query_cursor_start,
            query_cursor_next,
            query_rotation_wrapped,
            pages,
            per_page,
            max_unique_vacancies,
            max_full_fetches,
            search_delay_seconds,
            full_fetch_min_delay_seconds,
            full_fetch_max_delay_seconds,
            search_observation_count,
            unique_vacancy_count,
            candidate_count,
            full_fetch_attempted,
            full_fetched,
            evaluation_candidate_count,
            failed,
            stopped_on_captcha,
            started_at,
            finished_at,
            s3_prefix,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("observation run UPSERT returned no row")
    return UUID(str(row[0]))


async def upsert_vacancy_observation(
    conn: AsyncConnection[Any],
    **kwargs: Any,
) -> None:
    """Persist account-run provenance for one discovered vacancy."""

    cursor = await conn.execute(
        """
        INSERT INTO careerops.vacancy_observations AS vo (
            run_id, vacancy_id, full_fetch_status, matched_query_keys,
            matched_query_sets, query_page_uris, search_item_uri, vacancy_uri,
            evaluation_candidates_uri, observed_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id, vacancy_id)
        DO UPDATE SET
            full_fetch_status = EXCLUDED.full_fetch_status,
            matched_query_keys = EXCLUDED.matched_query_keys,
            matched_query_sets = EXCLUDED.matched_query_sets,
            query_page_uris = EXCLUDED.query_page_uris,
            search_item_uri = EXCLUDED.search_item_uri,
            vacancy_uri = EXCLUDED.vacancy_uri,
            evaluation_candidates_uri = EXCLUDED.evaluation_candidates_uri,
            observed_at = EXCLUDED.observed_at,
            updated_at = now()
        RETURNING vo.run_id
        """,
        (
            kwargs["run_id"],
            kwargs["vacancy_id"],
            kwargs["full_fetch_status"],
            list(kwargs["matched_query_keys"]),
            list(kwargs["matched_query_sets"]),
            list(kwargs["query_page_uris"]),
            kwargs["search_item_uri"],
            kwargs.get("vacancy_uri"),
            kwargs["evaluation_candidates_uri"],
            kwargs["observed_at"],
        ),
    )
    if await cursor.fetchone() is None:
        raise RuntimeError("vacancy observation UPSERT returned no row")


async def upsert_evaluation_work_item(
    conn: AsyncConnection[Any],
    **kwargs: Any,
) -> None:
    """Persist one explicit vacancy x resume future-evaluation identity."""

    cursor = await conn.execute(
        """
        INSERT INTO careerops.evaluation_work_items AS ewi (
            run_id, vacancy_id, resume_id, binding_key, target_key,
            binding_version, auto_apply, matched_query_keys, matched_query_sets,
            resume_query_sets, overlap_query_keys, overlap_query_sets,
            has_provenance_overlap, full_fetch_status, evaluation_status, created_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (run_id, vacancy_id, resume_id)
        DO UPDATE SET
            binding_key = EXCLUDED.binding_key,
            target_key = EXCLUDED.target_key,
            binding_version = EXCLUDED.binding_version,
            auto_apply = EXCLUDED.auto_apply,
            matched_query_keys = EXCLUDED.matched_query_keys,
            matched_query_sets = EXCLUDED.matched_query_sets,
            resume_query_sets = EXCLUDED.resume_query_sets,
            overlap_query_keys = EXCLUDED.overlap_query_keys,
            overlap_query_sets = EXCLUDED.overlap_query_sets,
            has_provenance_overlap = EXCLUDED.has_provenance_overlap,
            full_fetch_status = EXCLUDED.full_fetch_status,
            evaluation_status = EXCLUDED.evaluation_status,
            created_at = EXCLUDED.created_at,
            updated_at = now()
        RETURNING ewi.run_id
        """,
        (
            kwargs["run_id"],
            kwargs["vacancy_id"],
            kwargs["resume_id"],
            kwargs["binding_key"],
            kwargs["target_key"],
            kwargs["binding_version"],
            kwargs["auto_apply"],
            list(kwargs["matched_query_keys"]),
            list(kwargs["matched_query_sets"]),
            list(kwargs["resume_query_sets"]),
            list(kwargs["overlap_query_keys"]),
            list(kwargs["overlap_query_sets"]),
            kwargs["has_provenance_overlap"],
            kwargs["full_fetch_status"],
            kwargs["evaluation_status"],
            kwargs["created_at"],
        ),
    )
    if await cursor.fetchone() is None:
        raise RuntimeError("evaluation work item UPSERT returned no row")


class PostgresApplicationClaimStore:
    """PostgreSQL-backed atomic application claim state machine."""

    def __init__(self, conn: AsyncConnection[Any]) -> None:
        self.conn = conn

    async def prepare_identity(self, **kwargs: Any) -> None:
        async with self.conn.transaction():
            await prepare_application_claim_identity(self.conn, **kwargs)

    async def acquire(self, **kwargs: Any) -> ApplicationClaimAcquisition:
        async with self.conn.transaction():
            return await acquire_application_claim(self.conn, **kwargs)

    async def transition(self, **kwargs: Any) -> ApplicationClaimRecord:
        async with self.conn.transaction():
            return await transition_application_claim(self.conn, **kwargs)


class PostgresObserveQueryCursorStore:
    """PostgreSQL-backed, profile-stable OBSERVE query rotation state."""

    def __init__(self, conn: AsyncConnection[Any]) -> None:
        self.conn = conn

    async def reserve(self, **kwargs: Any) -> ObserveQueryCursorReservation:
        return await reserve_observe_query_window(self.conn, **kwargs)


class PostgresResumeRegistry:
    """Primary runtime persistence for authoritative HH resume reconciliation."""

    def __init__(self, conn: AsyncConnection[Any]) -> None:
        self.conn = conn

    async def load(
        self,
        *,
        account_key: str,
        source_profile: str,
    ) -> AccountResumeInventory | None:
        cursor = await self.conn.execute(
            """
            SELECT
                r.source_resume_id,
                r.title,
                r.upstream_status,
                r.lifecycle,
                r.first_seen_at,
                r.last_seen_at,
                r.inactive_at,
                r.binding_key,
                r.binding_enabled,
                r.target_key,
                r.query_sets,
                r.auto_apply,
                r.binding_version,
                r.content_hash,
                r.source_payload
            FROM careerops.resumes AS r
            JOIN careerops.source_profiles AS sp ON sp.id = r.source_profile_id
            WHERE sp.source = 'hh'
              AND sp.profile_key = %s
              AND sp.account_key = %s
            ORDER BY r.source_resume_id
            """,
            (source_profile, account_key),
        )
        rows = await cursor.fetchall()
        if not rows:
            return None
        resumes = tuple(
            ReconciledResume(
                source_profile=source_profile,
                source_resume_id=str(row[0]),
                current_title=row[1],
                upstream_status=row[2],
                lifecycle=ResumeLifecycle(str(row[3])),
                first_seen_at=row[4],
                last_seen_at=row[5],
                inactive_at=row[6],
                binding_key=row[7],
                binding_enabled=bool(row[8]),
                target_key=row[9],
                query_sets=tuple(row[10] or ()),
                auto_apply=bool(row[11]),
                binding_version=row[12],
                content_sha256=str(row[13]),
                source_payload=dict(row[14] or {}),
            )
            for row in rows
        )
        return AccountResumeInventory(
            account_key=account_key,
            source_profile=source_profile,
            reconciled_at=max(
                resume.inactive_at or resume.last_seen_at for resume in resumes
            ),
            resumes=resumes,
        )

    async def save(self, inventory: AccountResumeInventory) -> None:
        async with self.conn.transaction():
            source_profile_id = await upsert_source_profile(
                self.conn,
                source="hh",
                profile_key=inventory.source_profile,
                account_key=inventory.account_key,
            )
            for resume in inventory.resumes:
                await upsert_reconciled_resume(
                    self.conn,
                    source_profile_id=source_profile_id,
                    resume=resume,
                )


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

    async def upsert_observation_run(self, **kwargs: Any) -> UUID:
        """Persist one account-wide observation run."""

        return await upsert_observation_run(self.conn, **kwargs)

    async def upsert_vacancy_observation(self, **kwargs: Any) -> None:
        """Persist one vacancy provenance record for an observation run."""

        await upsert_vacancy_observation(self.conn, **kwargs)

    async def upsert_evaluation_work_item(self, **kwargs: Any) -> None:
        """Persist one vacancy x resume evaluation work item."""

        await upsert_evaluation_work_item(self.conn, **kwargs)
