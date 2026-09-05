from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, DOUBLE_PRECISION, JSONB, TIMESTAMP, UUID

CAREEROPS_SCHEMA = "careerops"

metadata = MetaData(schema=CAREEROPS_SCHEMA)

source_profiles = Table(
    "source_profiles",
    metadata,
    Column("id", BigInteger, Identity(always=False), primary_key=True, nullable=False),
    Column("source", Text, nullable=False),
    Column("profile_key", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("account_key", Text),
    UniqueConstraint(
        "source",
        "profile_key",
        name="source_profiles_source_profile_key_uk",
    ),
)

resumes = Table(
    "resumes",
    metadata,
    Column("id", BigInteger, Identity(always=False), primary_key=True, nullable=False),
    Column(
        "source_profile_id",
        BigInteger,
        ForeignKey("careerops.source_profiles.id"),
        nullable=False,
    ),
    Column("source_resume_id", Text, nullable=False),
    Column("title", Text),
    Column("raw_uri", Text),
    Column("content_hash", Text),
    Column("first_seen_at", TIMESTAMP(timezone=True), nullable=False),
    Column("last_seen_at", TIMESTAMP(timezone=True), nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("upstream_status", Text),
    Column(
        "lifecycle",
        Text,
        nullable=False,
        server_default=text("'active'::text"),
    ),
    Column("present_in_upstream", Boolean, nullable=False, server_default=text("true")),
    Column("inactive_at", TIMESTAMP(timezone=True)),
    Column("binding_key", Text),
    Column("binding_version", Integer),
    Column("target_key", Text),
    Column("binding_enabled", Boolean, nullable=False, server_default=text("false")),
    Column("auto_apply", Boolean, nullable=False, server_default=text("false")),
    Column(
        "selectable_for_evaluation",
        Boolean,
        nullable=False,
        server_default=text("false"),
    ),
    Column(
        "selectable_for_auto_apply",
        Boolean,
        nullable=False,
        server_default=text("false"),
    ),
    Column(
        "query_sets",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column(
        "source_payload",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    ),
    UniqueConstraint(
        "source_profile_id",
        "source_resume_id",
        name="resumes_source_profile_resume_uk",
    ),
    CheckConstraint("last_seen_at >= first_seen_at", name="resumes_seen_order_ck"),
    CheckConstraint(
        "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'",
        name="resumes_content_hash_ck",
    ),
    CheckConstraint(
        "lifecycle IN ('active', 'deleted')",
        name="resumes_lifecycle_ck",
    ),
    CheckConstraint(
        "binding_version IS NULL OR binding_version >= 1",
        name="resumes_binding_version_ck",
    ),
    CheckConstraint(
        """
        (
            lifecycle = 'active'
            AND present_in_upstream
            AND inactive_at IS NULL
        )
        OR (
            lifecycle = 'deleted'
            AND NOT present_in_upstream
            AND inactive_at IS NOT NULL
        )
        """,
        name="resumes_lifecycle_state_ck",
    ),
    CheckConstraint(
        """
        NOT selectable_for_evaluation
        OR (
            lifecycle = 'active'
            AND present_in_upstream
            AND binding_enabled
            AND binding_key IS NOT NULL
            AND target_key IS NOT NULL
        )
        """,
        name="resumes_evaluation_selection_ck",
    ),
    CheckConstraint(
        """
        NOT selectable_for_auto_apply
        OR (
            lifecycle = 'active'
            AND present_in_upstream
            AND binding_enabled
            AND binding_key IS NOT NULL
            AND target_key IS NOT NULL
            AND auto_apply
            AND selectable_for_evaluation
            AND upstream_status = 'published'
        )
        """,
        name="resumes_auto_apply_selection_ck",
    ),
)

vacancies = Table(
    "vacancies",
    metadata,
    Column("id", BigInteger, Identity(always=False), primary_key=True, nullable=False),
    Column("source", Text, nullable=False),
    Column("source_entity_id", Text, nullable=False),
    Column("source_employer_id", Text),
    Column("title", Text),
    Column("company_name", Text),
    Column("description", Text),
    Column("salary_from", Numeric),
    Column("salary_to", Numeric),
    Column("salary_currency", Text),
    Column("location", Text),
    Column("remote", Boolean),
    Column("employment_type", Text),
    Column("experience", Text),
    Column("source_url", Text),
    Column("relations", ARRAY(Text)),
    Column("archived", Boolean),
    Column("closed_for_applicants", Boolean),
    Column("has_test", Boolean),
    Column("response_letter_required", Boolean),
    Column("response_url", Text),
    Column("published_at", TIMESTAMP(timezone=True)),
    Column("first_seen_at", TIMESTAMP(timezone=True), nullable=False),
    Column("last_seen_at", TIMESTAMP(timezone=True), nullable=False),
    Column("latest_raw_uri", Text, nullable=False),
    Column("latest_content_hash", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("source", "source_entity_id", name="vacancies_source_entity_uk"),
    CheckConstraint("last_seen_at >= first_seen_at", name="vacancies_seen_order_ck"),
    CheckConstraint(
        "latest_content_hash ~ '^[0-9a-f]{64}$'",
        name="vacancies_latest_content_hash_ck",
    ),
)

batch_runs = Table(
    "batch_runs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
    Column("resume_id", BigInteger, ForeignKey("careerops.resumes.id"), nullable=False),
    Column("search_query", Text),
    Column("area_id", Text),
    Column("period_days", Integer),
    Column("pages", Integer),
    Column("per_page", Integer),
    Column("max_responses", Integer),
    Column(
        "professional_roles",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column("cover_letter_mode", Text),
    Column("live", Boolean, nullable=False),
    Column("status", Text, nullable=False),
    Column("discovered", Integer),
    Column("prefiltered", Integer),
    Column("full_fetched", Integer),
    Column("accepted", Integer),
    Column("submitted", Integer),
    Column("confirmed", Integer),
    Column("failed", Integer),
    Column("stopped_on_captcha", Boolean),
    Column("started_at", TIMESTAMP(timezone=True), nullable=False),
    Column("finished_at", TIMESTAMP(timezone=True)),
    Column("s3_prefix", Text),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint(
        "status IN ('running', 'incomplete', 'finished')",
        name="batch_runs_status_ck",
    ),
    CheckConstraint(
        "finished_at IS NULL OR finished_at >= started_at",
        name="batch_runs_time_order_ck",
    ),
    CheckConstraint(
        "status <> 'finished' OR finished_at IS NOT NULL",
        name="batch_runs_finished_at_ck",
    ),
    CheckConstraint(
        """
        (discovered IS NULL OR discovered >= 0)
        AND (prefiltered IS NULL OR prefiltered >= 0)
        AND (full_fetched IS NULL OR full_fetched >= 0)
        AND (accepted IS NULL OR accepted >= 0)
        AND (submitted IS NULL OR submitted >= 0)
        AND (confirmed IS NULL OR confirmed >= 0)
        AND (failed IS NULL OR failed >= 0)
        """,
        name="batch_runs_counters_ck",
    ),
)

vacancy_decisions = Table(
    "vacancy_decisions",
    metadata,
    Column("id", BigInteger, Identity(always=False), primary_key=True, nullable=False),
    Column("run_id", UUID(as_uuid=True), ForeignKey("careerops.batch_runs.id"), nullable=False),
    Column(
        "vacancy_id",
        BigInteger,
        ForeignKey("careerops.vacancies.id"),
        nullable=False,
    ),
    Column("stage", Text, nullable=False),
    Column("accepted", Boolean, nullable=False),
    Column("reason", Text, nullable=False),
    Column(
        "matched_domains",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column(
        "blocked_terms",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False),
    UniqueConstraint(
        "run_id",
        "vacancy_id",
        "stage",
        name="vacancy_decisions_run_vacancy_stage_uk",
    ),
)

applications = Table(
    "applications",
    metadata,
    Column("id", BigInteger, Identity(always=False), primary_key=True, nullable=False),
    Column("application_run_id", UUID(as_uuid=True), nullable=False),
    Column("batch_run_id", UUID(as_uuid=True), ForeignKey("careerops.batch_runs.id")),
    Column(
        "vacancy_id",
        BigInteger,
        ForeignKey("careerops.vacancies.id"),
        nullable=False,
    ),
    Column("resume_id", BigInteger, ForeignKey("careerops.resumes.id"), nullable=False),
    Column("submission_mode", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("confirmed", Boolean),
    Column("requested_at", TIMESTAMP(timezone=True), nullable=False),
    Column("finished_at", TIMESTAMP(timezone=True)),
    Column("cover_letter_uri", Text),
    Column("request_uri", Text),
    Column("result_uri", Text),
    Column("before_uri", Text),
    Column("after_uri", Text),
    Column("error_type", Text),
    Column("error_message", Text),
    Column(
        "upstream_metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    ),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("application_run_id", name="applications_application_run_uk"),
    UniqueConstraint("vacancy_id", "resume_id", name="applications_vacancy_resume_uk"),
    CheckConstraint(
        "finished_at IS NULL OR finished_at >= requested_at",
        name="applications_time_order_ck",
    ),
)

application_claims = Table(
    "application_claims",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
    Column("account_key", Text, nullable=False),
    Column("resume_id", BigInteger, ForeignKey("careerops.resumes.id"), nullable=False),
    Column(
        "vacancy_id",
        BigInteger,
        ForeignKey("careerops.vacancies.id"),
        nullable=False,
    ),
    Column("application_run_id", UUID(as_uuid=True), nullable=False),
    Column("status", Text, nullable=False),
    Column("attempt_count", Integer, nullable=False, server_default=text("1")),
    Column("claimed_at", TIMESTAMP(timezone=True), nullable=False),
    Column("state_changed_at", TIMESTAMP(timezone=True), nullable=False),
    Column("submitted_at", TIMESTAMP(timezone=True)),
    Column("finished_at", TIMESTAMP(timezone=True)),
    Column("last_error_type", Text),
    Column("last_error_message", Text),
    Column(
        "upstream_evidence",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    ),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("resume_id", "vacancy_id", name="application_claims_identity_uk"),
    CheckConstraint(
        """
        status IN (
            'CLAIMED',
            'SUBMITTING',
            'SUBMITTED',
            'UNCERTAIN',
            'FAILED_SAFE_TO_RETRY'
        )
        """,
        name="application_claims_status_ck",
    ),
    CheckConstraint("attempt_count >= 1", name="application_claims_attempt_count_ck"),
    CheckConstraint(
        """
        state_changed_at >= claimed_at
        AND (submitted_at IS NULL OR submitted_at >= claimed_at)
        AND (finished_at IS NULL OR finished_at >= claimed_at)
        """,
        name="application_claims_time_order_ck",
    ),
)

observe_query_cursors = Table(
    "observe_query_cursors",
    metadata,
    Column(
        "source_profile_id",
        BigInteger,
        ForeignKey("careerops.source_profiles.id"),
        primary_key=True,
        nullable=False,
    ),
    Column("account_key", Text, nullable=False),
    Column("catalog_signature", Text, nullable=False),
    Column("catalog_size", Integer, nullable=False),
    Column("next_query_offset", Integer, nullable=False),
    Column("last_window_start", Integer, nullable=False),
    Column("last_window_size", Integer, nullable=False),
    Column("last_run_id", UUID(as_uuid=True), nullable=False),
    Column("last_reserved_at", TIMESTAMP(timezone=True), nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint(
        "catalog_signature ~ '^[0-9a-f]{64}$'",
        name="observe_query_cursors_signature_ck",
    ),
    CheckConstraint("catalog_size >= 1", name="observe_query_cursors_catalog_size_ck"),
    CheckConstraint(
        """
        next_query_offset >= 0
        AND next_query_offset < catalog_size
        AND last_window_start >= 0
        AND last_window_start < catalog_size
        """,
        name="observe_query_cursors_offset_ck",
    ),
    CheckConstraint(
        "last_window_size >= 1 AND last_window_size <= catalog_size",
        name="observe_query_cursors_window_ck",
    ),
)

search_query_states = Table(
    "search_query_states",
    metadata,
    Column(
        "id",
        BigInteger,
        Identity(always=False),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "source_profile_id",
        BigInteger,
        ForeignKey("careerops.source_profiles.id"),
        nullable=False,
    ),
    Column("account_key", Text, nullable=False),
    Column("query_key", Text, nullable=False),
    Column("query_set_key", Text, nullable=False),
    Column("query_signature", Text, nullable=False),
    Column("request_params", JSONB, nullable=False),
    Column(
        "is_active",
        Boolean,
        nullable=False,
        server_default=text("true"),
    ),
    Column("retired_at", TIMESTAMP(timezone=True)),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    Column(
        "updated_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    UniqueConstraint(
        "source_profile_id",
        "query_key",
        "query_signature",
        name="search_query_states_profile_query_signature_uk",
    ),
    CheckConstraint(
        "query_signature ~ '^[0-9a-f]{64}$'",
        name="search_query_states_signature_ck",
    ),
    CheckConstraint(
        """
        jsonb_typeof(request_params) = 'object'
        """,
        name="search_query_states_request_params_ck",
    ),
    CheckConstraint(
        """
        btrim(account_key) <> ''
        AND btrim(query_key) <> ''
        AND btrim(query_set_key) <> ''
        """,
        name="search_query_states_keys_ck",
    ),
    CheckConstraint(
        """
        (
            is_active
            AND retired_at IS NULL
        )
        OR (
            NOT is_active
            AND retired_at IS NOT NULL
        )
        """,
        name="search_query_states_lifecycle_ck",
    ),
)


observation_runs = Table(
    "observation_runs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
    Column(
        "source_profile_id",
        BigInteger,
        ForeignKey("careerops.source_profiles.id"),
        nullable=False,
    ),
    Column("account_key", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column(
        "query_set_keys",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column(
        "query_keys",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column("query_catalog_size", Integer, nullable=False),
    Column("query_catalog_signature", Text, nullable=False),
    Column("max_queries_per_run", Integer, nullable=False),
    Column("query_cursor_start", Integer, nullable=False),
    Column("query_cursor_next", Integer, nullable=False),
    Column("query_rotation_wrapped", Boolean, nullable=False),
    Column("pages", Integer, nullable=False),
    Column("per_page", Integer, nullable=False),
    Column("max_unique_vacancies", Integer, nullable=False),
    Column("max_full_fetches", Integer, nullable=False),
    Column("search_delay_seconds", DOUBLE_PRECISION, nullable=False),
    Column("full_fetch_min_delay_seconds", DOUBLE_PRECISION, nullable=False),
    Column("full_fetch_max_delay_seconds", DOUBLE_PRECISION, nullable=False),
    Column("search_observation_count", Integer),
    Column("unique_vacancy_count", Integer),
    Column("candidate_count", Integer),
    Column("full_fetch_attempted", Integer),
    Column("full_fetched", Integer),
    Column("evaluation_candidate_count", Integer),
    Column("failed", Integer),
    Column("stopped_on_captcha", Boolean),
    Column("started_at", TIMESTAMP(timezone=True), nullable=False),
    Column("finished_at", TIMESTAMP(timezone=True)),
    Column("s3_prefix", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint(
        "status IN ('running', 'incomplete', 'finished')",
        name="observation_runs_status_ck",
    ),
    CheckConstraint(
        "finished_at IS NULL OR finished_at >= started_at",
        name="observation_runs_time_order_ck",
    ),
    CheckConstraint(
        """
        query_catalog_size >= 1
        AND max_queries_per_run >= 1
        AND cardinality(query_keys) >= 1
        AND cardinality(query_keys) <= max_queries_per_run
        AND cardinality(query_keys) <= query_catalog_size
        AND query_cursor_start >= 0
        AND query_cursor_start < query_catalog_size
        AND query_cursor_next >= 0
        AND query_cursor_next < query_catalog_size
        AND query_catalog_signature ~ '^[0-9a-f]{64}$'
        """,
        name="observation_runs_query_rotation_ck",
    ),
)

vacancy_observations = Table(
    "vacancy_observations",
    metadata,
    Column(
        "run_id",
        UUID(as_uuid=True),
        ForeignKey("careerops.observation_runs.id"),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "vacancy_id",
        BigInteger,
        ForeignKey("careerops.vacancies.id"),
        primary_key=True,
        nullable=False,
    ),
    Column("full_fetch_status", Text, nullable=False),
    Column(
        "matched_query_keys",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column(
        "matched_query_sets",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column(
        "query_page_uris",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column("search_item_uri", Text, nullable=False),
    Column("vacancy_uri", Text),
    Column("evaluation_candidates_uri", Text, nullable=False),
    Column("observed_at", TIMESTAMP(timezone=True), nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
)

evaluation_work_items = Table(
    "evaluation_work_items",
    metadata,
    Column(
        "run_id",
        UUID(as_uuid=True),
        ForeignKey("careerops.observation_runs.id"),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "vacancy_id",
        BigInteger,
        ForeignKey("careerops.vacancies.id"),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "resume_id",
        BigInteger,
        ForeignKey("careerops.resumes.id"),
        primary_key=True,
        nullable=False,
    ),
    Column("binding_key", Text, nullable=False),
    Column("target_key", Text, nullable=False),
    Column("binding_version", Integer, nullable=False),
    Column("auto_apply", Boolean, nullable=False),
    Column(
        "matched_query_keys",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column(
        "matched_query_sets",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column(
        "resume_query_sets",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column(
        "overlap_query_keys",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column(
        "overlap_query_sets",
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    ),
    Column("has_provenance_overlap", Boolean, nullable=False),
    Column("full_fetch_status", Text, nullable=False),
    Column("evaluation_status", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint("binding_version >= 1", name="evaluation_work_items_binding_version_ck"),
)

Index("batch_runs_started_at_idx", batch_runs.c.started_at.desc())
Index(
    "batch_runs_status_started_at_idx",
    batch_runs.c.status,
    batch_runs.c.started_at.desc(),
)
Index("vacancies_last_seen_at_idx", vacancies.c.last_seen_at.desc())
Index(
    "vacancies_source_employer_idx",
    vacancies.c.source,
    vacancies.c.source_employer_id,
    postgresql_where=vacancies.c.source_employer_id.is_not(None),
)
Index("vacancy_decisions_run_id_idx", vacancy_decisions.c.run_id)
Index(
    "vacancy_decisions_vacancy_created_at_idx",
    vacancy_decisions.c.vacancy_id,
    vacancy_decisions.c.created_at.desc(),
)
Index(
    "applications_batch_run_id_idx",
    applications.c.batch_run_id,
    postgresql_where=applications.c.batch_run_id.is_not(None),
)
Index("applications_requested_at_idx", applications.c.requested_at.desc())
Index(
    "application_claims_status_changed_idx",
    application_claims.c.status,
    application_claims.c.state_changed_at.desc(),
)
Index(
    "source_profiles_source_account_uk",
    source_profiles.c.source,
    source_profiles.c.account_key,
    unique=True,
    postgresql_where=source_profiles.c.account_key.is_not(None),
)
Index(
    "search_query_states_active_profile_query_uk",
    search_query_states.c.source_profile_id,
    search_query_states.c.query_key,
    unique=True,
    postgresql_where=search_query_states.c.is_active.is_(True),
)
Index(
    "observation_runs_account_started_idx",
    observation_runs.c.account_key,
    observation_runs.c.started_at.desc(),
)
Index(
    "vacancy_observations_vacancy_idx",
    vacancy_observations.c.vacancy_id,
    vacancy_observations.c.observed_at.desc(),
)
Index(
    "evaluation_work_items_resume_status_idx",
    evaluation_work_items.c.resume_id,
    evaluation_work_items.c.evaluation_status,
    evaluation_work_items.c.created_at.desc(),
)

__all__ = ["CAREEROPS_SCHEMA", "metadata"]
