"""Current schema baseline after legacy migration 0005.

Revision ID: 20260904_0005
Revises:
Create Date: 2026-09-04

Existing CareerOPS databases that already contain legacy migrations 0001-0005
must be stamped at this revision instead of running this upgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260904_0005"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "careerops"


def upgrade() -> None:
    """Create the complete effective schema after legacy migration 0005."""

    op.execute(sa.schema.CreateSchema(SCHEMA))

    op.create_table(
        "source_profiles",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("profile_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("account_key", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "profile_key",
            name="source_profiles_source_profile_key_uk",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "resumes",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("source_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("source_resume_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("raw_uri", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("upstream_status", sa.Text(), nullable=True),
        sa.Column(
            "lifecycle",
            sa.Text(),
            server_default=sa.text("'active'::text"),
            nullable=False,
        ),
        sa.Column(
            "present_in_upstream",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "inactive_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("binding_key", sa.Text(), nullable=True),
        sa.Column("binding_version", sa.Integer(), nullable=True),
        sa.Column("target_key", sa.Text(), nullable=True),
        sa.Column(
            "binding_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "auto_apply",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "selectable_for_evaluation",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "selectable_for_auto_apply",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "query_sets",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "source_payload",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="resumes_seen_order_ck",
        ),
        sa.CheckConstraint(
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'",
            name="resumes_content_hash_ck",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('active', 'deleted')",
            name="resumes_lifecycle_ck",
        ),
        sa.CheckConstraint(
            "binding_version IS NULL OR binding_version >= 1",
            name="resumes_binding_version_ck",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
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
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(
            ["source_profile_id"],
            ["careerops.source_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_profile_id",
            "source_resume_id",
            name="resumes_source_profile_resume_uk",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "vacancies",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_entity_id", sa.Text(), nullable=False),
        sa.Column("source_employer_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("company_name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("salary_from", sa.Numeric(), nullable=True),
        sa.Column("salary_to", sa.Numeric(), nullable=True),
        sa.Column("salary_currency", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("remote", sa.Boolean(), nullable=True),
        sa.Column("employment_type", sa.Text(), nullable=True),
        sa.Column("experience", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("relations", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=True),
        sa.Column("closed_for_applicants", sa.Boolean(), nullable=True),
        sa.Column("has_test", sa.Boolean(), nullable=True),
        sa.Column("response_letter_required", sa.Boolean(), nullable=True),
        sa.Column("response_url", sa.Text(), nullable=True),
        sa.Column(
            "published_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "first_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column("latest_raw_uri", sa.Text(), nullable=False),
        sa.Column("latest_content_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="vacancies_seen_order_ck",
        ),
        sa.CheckConstraint(
            "latest_content_hash ~ '^[0-9a-f]{64}$'",
            name="vacancies_latest_content_hash_ck",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "source_entity_id",
            name="vacancies_source_entity_uk",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "batch_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resume_id", sa.BigInteger(), nullable=False),
        sa.Column("search_query", sa.Text(), nullable=True),
        sa.Column("area_id", sa.Text(), nullable=True),
        sa.Column("period_days", sa.Integer(), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("per_page", sa.Integer(), nullable=True),
        sa.Column("max_responses", sa.Integer(), nullable=True),
        sa.Column(
            "professional_roles",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("cover_letter_mode", sa.Text(), nullable=True),
        sa.Column("live", sa.Boolean(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("discovered", sa.Integer(), nullable=True),
        sa.Column("prefiltered", sa.Integer(), nullable=True),
        sa.Column("full_fetched", sa.Integer(), nullable=True),
        sa.Column("accepted", sa.Integer(), nullable=True),
        sa.Column("submitted", sa.Integer(), nullable=True),
        sa.Column("confirmed", sa.Integer(), nullable=True),
        sa.Column("failed", sa.Integer(), nullable=True),
        sa.Column("stopped_on_captcha", sa.Boolean(), nullable=True),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "finished_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("s3_prefix", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'incomplete', 'finished')",
            name="batch_runs_status_ck",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="batch_runs_time_order_ck",
        ),
        sa.CheckConstraint(
            "status <> 'finished' OR finished_at IS NOT NULL",
            name="batch_runs_finished_at_ck",
        ),
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(["resume_id"], ["careerops.resumes.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )

    op.create_table(
        "vacancy_decisions",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "matched_domains",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "blocked_terms",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["careerops.batch_runs.id"]),
        sa.ForeignKeyConstraint(["vacancy_id"], ["careerops.vacancies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "vacancy_id",
            "stage",
            name="vacancy_decisions_run_vacancy_stage_uk",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "applications",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column(
            "application_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "batch_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("resume_id", sa.BigInteger(), nullable=False),
        sa.Column("submission_mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=True),
        sa.Column(
            "requested_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "finished_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("cover_letter_uri", sa.Text(), nullable=True),
        sa.Column("request_uri", sa.Text(), nullable=True),
        sa.Column("result_uri", sa.Text(), nullable=True),
        sa.Column("before_uri", sa.Text(), nullable=True),
        sa.Column("after_uri", sa.Text(), nullable=True),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "upstream_metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= requested_at",
            name="applications_time_order_ck",
        ),
        sa.ForeignKeyConstraint(["batch_run_id"], ["careerops.batch_runs.id"]),
        sa.ForeignKeyConstraint(["vacancy_id"], ["careerops.vacancies.id"]),
        sa.ForeignKeyConstraint(["resume_id"], ["careerops.resumes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_run_id",
            name="applications_application_run_uk",
        ),
        sa.UniqueConstraint(
            "vacancy_id",
            "resume_id",
            name="applications_vacancy_resume_uk",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "application_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("resume_id", sa.BigInteger(), nullable=False),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "application_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "claimed_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "state_changed_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "submitted_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "finished_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("last_error_type", sa.Text(), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "upstream_evidence",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "attempt_count >= 1",
            name="application_claims_attempt_count_ck",
        ),
        sa.CheckConstraint(
            """
            state_changed_at >= claimed_at
            AND (submitted_at IS NULL OR submitted_at >= claimed_at)
            AND (finished_at IS NULL OR finished_at >= claimed_at)
            """,
            name="application_claims_time_order_ck",
        ),
        sa.ForeignKeyConstraint(["resume_id"], ["careerops.resumes.id"]),
        sa.ForeignKeyConstraint(["vacancy_id"], ["careerops.vacancies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resume_id",
            "vacancy_id",
            name="application_claims_identity_uk",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "observe_query_cursors",
        sa.Column("source_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("catalog_signature", sa.Text(), nullable=False),
        sa.Column("catalog_size", sa.Integer(), nullable=False),
        sa.Column("next_query_offset", sa.Integer(), nullable=False),
        sa.Column("last_window_start", sa.Integer(), nullable=False),
        sa.Column("last_window_size", sa.Integer(), nullable=False),
        sa.Column(
            "last_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "last_reserved_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "catalog_signature ~ '^[0-9a-f]{64}$'",
            name="observe_query_cursors_signature_ck",
        ),
        sa.CheckConstraint(
            "catalog_size >= 1",
            name="observe_query_cursors_catalog_size_ck",
        ),
        sa.CheckConstraint(
            """
            next_query_offset >= 0
            AND next_query_offset < catalog_size
            AND last_window_start >= 0
            AND last_window_start < catalog_size
            """,
            name="observe_query_cursors_offset_ck",
        ),
        sa.CheckConstraint(
            "last_window_size >= 1 AND last_window_size <= catalog_size",
            name="observe_query_cursors_window_ck",
        ),
        sa.ForeignKeyConstraint(
            ["source_profile_id"],
            ["careerops.source_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("source_profile_id"),
        schema=SCHEMA,
    )

    op.create_table(
        "observation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "query_set_keys",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "query_keys",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("query_catalog_size", sa.Integer(), nullable=False),
        sa.Column("query_catalog_signature", sa.Text(), nullable=False),
        sa.Column("max_queries_per_run", sa.Integer(), nullable=False),
        sa.Column("query_cursor_start", sa.Integer(), nullable=False),
        sa.Column("query_cursor_next", sa.Integer(), nullable=False),
        sa.Column("query_rotation_wrapped", sa.Boolean(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("per_page", sa.Integer(), nullable=False),
        sa.Column("max_unique_vacancies", sa.Integer(), nullable=False),
        sa.Column("max_full_fetches", sa.Integer(), nullable=False),
        sa.Column(
            "search_delay_seconds",
            postgresql.DOUBLE_PRECISION(),
            nullable=False,
        ),
        sa.Column(
            "full_fetch_min_delay_seconds",
            postgresql.DOUBLE_PRECISION(),
            nullable=False,
        ),
        sa.Column(
            "full_fetch_max_delay_seconds",
            postgresql.DOUBLE_PRECISION(),
            nullable=False,
        ),
        sa.Column("search_observation_count", sa.Integer(), nullable=True),
        sa.Column("unique_vacancy_count", sa.Integer(), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=True),
        sa.Column("full_fetch_attempted", sa.Integer(), nullable=True),
        sa.Column("full_fetched", sa.Integer(), nullable=True),
        sa.Column("evaluation_candidate_count", sa.Integer(), nullable=True),
        sa.Column("failed", sa.Integer(), nullable=True),
        sa.Column("stopped_on_captcha", sa.Boolean(), nullable=True),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "finished_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("s3_prefix", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'incomplete', 'finished')",
            name="observation_runs_status_ck",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="observation_runs_time_order_ck",
        ),
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(
            ["source_profile_id"],
            ["careerops.source_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )

    op.create_table(
        "vacancy_observations",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("full_fetch_status", sa.Text(), nullable=False),
        sa.Column(
            "matched_query_keys",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "matched_query_sets",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "query_page_uris",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("search_item_uri", sa.Text(), nullable=False),
        sa.Column("vacancy_uri", sa.Text(), nullable=True),
        sa.Column("evaluation_candidates_uri", sa.Text(), nullable=False),
        sa.Column(
            "observed_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["careerops.observation_runs.id"]),
        sa.ForeignKeyConstraint(["vacancy_id"], ["careerops.vacancies.id"]),
        sa.PrimaryKeyConstraint("run_id", "vacancy_id"),
        schema=SCHEMA,
    )

    op.create_table(
        "evaluation_work_items",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("resume_id", sa.BigInteger(), nullable=False),
        sa.Column("binding_key", sa.Text(), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("binding_version", sa.Integer(), nullable=False),
        sa.Column("auto_apply", sa.Boolean(), nullable=False),
        sa.Column(
            "matched_query_keys",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "matched_query_sets",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "resume_query_sets",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "overlap_query_keys",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "overlap_query_sets",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("has_provenance_overlap", sa.Boolean(), nullable=False),
        sa.Column("full_fetch_status", sa.Text(), nullable=False),
        sa.Column("evaluation_status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "binding_version >= 1",
            name="evaluation_work_items_binding_version_ck",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["careerops.observation_runs.id"]),
        sa.ForeignKeyConstraint(["vacancy_id"], ["careerops.vacancies.id"]),
        sa.ForeignKeyConstraint(["resume_id"], ["careerops.resumes.id"]),
        sa.PrimaryKeyConstraint("run_id", "vacancy_id", "resume_id"),
        schema=SCHEMA,
    )

    op.create_index(
        "batch_runs_started_at_idx",
        "batch_runs",
        [sa.literal_column("started_at DESC")],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "batch_runs_status_started_at_idx",
        "batch_runs",
        ["status", sa.literal_column("started_at DESC")],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "vacancies_last_seen_at_idx",
        "vacancies",
        [sa.literal_column("last_seen_at DESC")],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "vacancies_source_employer_idx",
        "vacancies",
        ["source", "source_employer_id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("source_employer_id IS NOT NULL"),
    )
    op.create_index(
        "vacancy_decisions_run_id_idx",
        "vacancy_decisions",
        ["run_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "vacancy_decisions_vacancy_created_at_idx",
        "vacancy_decisions",
        ["vacancy_id", sa.literal_column("created_at DESC")],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "applications_batch_run_id_idx",
        "applications",
        ["batch_run_id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("batch_run_id IS NOT NULL"),
    )
    op.create_index(
        "applications_requested_at_idx",
        "applications",
        [sa.literal_column("requested_at DESC")],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "application_claims_status_changed_idx",
        "application_claims",
        ["status", sa.literal_column("state_changed_at DESC")],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "source_profiles_source_account_uk",
        "source_profiles",
        ["source", "account_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("account_key IS NOT NULL"),
    )
    op.create_index(
        "observation_runs_account_started_idx",
        "observation_runs",
        ["account_key", sa.literal_column("started_at DESC")],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "vacancy_observations_vacancy_idx",
        "vacancy_observations",
        ["vacancy_id", sa.literal_column("observed_at DESC")],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "evaluation_work_items_resume_status_idx",
        "evaluation_work_items",
        ["resume_id", "evaluation_status", sa.literal_column("created_at DESC")],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Remove the baseline objects and return a fresh database to empty state."""

    op.drop_index(
        "evaluation_work_items_resume_status_idx",
        table_name="evaluation_work_items",
        schema=SCHEMA,
    )
    op.drop_index(
        "vacancy_observations_vacancy_idx",
        table_name="vacancy_observations",
        schema=SCHEMA,
    )
    op.drop_index(
        "observation_runs_account_started_idx",
        table_name="observation_runs",
        schema=SCHEMA,
    )
    op.drop_index(
        "source_profiles_source_account_uk",
        table_name="source_profiles",
        schema=SCHEMA,
    )
    op.drop_index(
        "application_claims_status_changed_idx",
        table_name="application_claims",
        schema=SCHEMA,
    )
    op.drop_index(
        "applications_requested_at_idx",
        table_name="applications",
        schema=SCHEMA,
    )
    op.drop_index(
        "applications_batch_run_id_idx",
        table_name="applications",
        schema=SCHEMA,
    )
    op.drop_index(
        "vacancy_decisions_vacancy_created_at_idx",
        table_name="vacancy_decisions",
        schema=SCHEMA,
    )
    op.drop_index(
        "vacancy_decisions_run_id_idx",
        table_name="vacancy_decisions",
        schema=SCHEMA,
    )
    op.drop_index(
        "vacancies_source_employer_idx",
        table_name="vacancies",
        schema=SCHEMA,
    )
    op.drop_index(
        "vacancies_last_seen_at_idx",
        table_name="vacancies",
        schema=SCHEMA,
    )
    op.drop_index(
        "batch_runs_status_started_at_idx",
        table_name="batch_runs",
        schema=SCHEMA,
    )
    op.drop_index(
        "batch_runs_started_at_idx",
        table_name="batch_runs",
        schema=SCHEMA,
    )

    op.drop_table("evaluation_work_items", schema=SCHEMA)
    op.drop_table("vacancy_observations", schema=SCHEMA)
    op.drop_table("observation_runs", schema=SCHEMA)
    op.drop_table("observe_query_cursors", schema=SCHEMA)
    op.drop_table("application_claims", schema=SCHEMA)
    op.drop_table("applications", schema=SCHEMA)
    op.drop_table("vacancy_decisions", schema=SCHEMA)
    op.drop_table("batch_runs", schema=SCHEMA)
    op.drop_table("vacancies", schema=SCHEMA)
    op.drop_table("resumes", schema=SCHEMA)
    op.drop_table("source_profiles", schema=SCHEMA)

    op.execute(sa.schema.DropSchema(SCHEMA))
