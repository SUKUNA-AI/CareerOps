"""PostgreSQL v2 foundation for a new database; no v1 upgrade path."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260906_v2_0001"
down_revision = None
branch_labels = ("v2",)
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'careerops')
               OR to_regclass('public.alembic_version') IS NOT NULL THEN
                RAISE EXCEPTION 'V2 baseline refuses legacy database; provision a new target';
            END IF;
        END $$;
    """)
    op.execute(sa.schema.CreateSchema("careerops_v2"))
    op.create_table(
        "sources",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
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
        sa.CheckConstraint("length(btrim(source_key)) > 0", name=op.f("ck_sources_source_key")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("source_key", name=op.f("uq_sources_source_key")),
        schema="careerops_v2",
    )
    op.create_table(
        "accounts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("account_key", sa.Text(), nullable=False),
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
        sa.CheckConstraint("length(btrim(account_key)) > 0", name=op.f("ck_accounts_account_key")),
        sa.ForeignKeyConstraint(
            ["source_id"], ["careerops_v2.sources.id"], name=op.f("fk_accounts_source_id_sources")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
        sa.UniqueConstraint("id", "source_id", name=op.f("uq_accounts_id_source_id")),
        sa.UniqueConstraint(
            "source_id", "account_key", name=op.f("uq_accounts_source_id_account_key")
        ),
        schema="careerops_v2",
    )
    op.create_table(
        "employers",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("source_employer_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("site_url", sa.Text(), nullable=True),
        sa.Column(
            "materialization_state",
            sa.Text(),
            server_default=sa.text("'identity_only'"),
            nullable=False,
        ),
        sa.Column("observed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("raw_uri", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("normalization_version", sa.Text(), nullable=True),
        sa.Column("materialization_key", sa.Text(), nullable=True),
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
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_employers_content_hash"),
        ),
        sa.CheckConstraint(
            "materialization_state <> 'current' OR (observed_at IS NOT NULL AND "
            "raw_uri IS NOT NULL AND raw_uri LIKE 's3://%' AND content_hash IS NOT "
            "NULL AND normalization_version IS NOT NULL AND "
            "length(btrim(normalization_version)) > 0 AND materialization_key IS NOT "
            "NULL AND length(btrim(materialization_key)) > 0)",
            name=op.f("ck_employers_current_provenance"),
        ),
        sa.CheckConstraint(
            "materialization_state IN ('identity_only', 'current', 'unavailable')",
            name=op.f("ck_employers_materialization_state"),
        ),
        sa.CheckConstraint(
            "length(btrim(source_employer_id)) > 0", name=op.f("ck_employers_external_identity")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["careerops_v2.sources.id"], name=op.f("fk_employers_source_id_sources")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employers")),
        sa.UniqueConstraint("id", "source_id", name=op.f("uq_employers_id_source_id")),
        sa.UniqueConstraint(
            "source_id",
            "source_employer_id",
            name=op.f("uq_employers_source_id_source_employer_id"),
        ),
        schema="careerops_v2",
    )
    op.create_table(
        "profiles",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
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
        sa.CheckConstraint("length(btrim(profile_key)) > 0", name=op.f("ck_profiles_profile_key")),
        sa.ForeignKeyConstraint(
            ["account_id", "source_id"],
            ["careerops_v2.accounts.id", "careerops_v2.accounts.source_id"],
            name=op.f("fk_profiles_account_id_accounts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profiles")),
        sa.UniqueConstraint(
            "id", "account_id", "source_id", name=op.f("uq_profiles_id_account_id_source_id")
        ),
        sa.UniqueConstraint(
            "source_id", "profile_key", name=op.f("uq_profiles_source_id_profile_key")
        ),
        schema="careerops_v2",
    )
    op.create_index(
        "ix_profiles_account", "profiles", ["account_id"], unique=False, schema="careerops_v2"
    )
    op.create_table(
        "source_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("task_key", sa.Text(), nullable=False),
        sa.Column("task_kind", sa.Text(), nullable=False),
        sa.Column("parent_task_id", sa.UUID(), nullable=True),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_token", sa.UUID(), nullable=True),
        sa.Column("leased_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("lease_expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_category", sa.Text(), nullable=True),
        sa.Column("result_artifact_uri", sa.Text(), nullable=True),
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
            "(status IN ('claimed', 'running') AND lease_owner IS NOT NULL AND "
            "length(btrim(lease_owner)) > 0 AND lease_token IS NOT NULL AND leased_at "
            "IS NOT NULL AND lease_expires_at IS NOT NULL AND lease_expires_at > "
            "leased_at AND attempt_count > 0) OR (status NOT IN ('claimed', "
            "'running') AND lease_owner IS NULL AND lease_token IS NULL AND leased_at "
            "IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_source_tasks_lease"),
        ),
        sa.CheckConstraint(
            "(status IN ('pending', 'deferred', 'retryable_failure') AND "
            "next_attempt_at IS NOT NULL) OR (status NOT IN ('pending', 'deferred', "
            "'retryable_failure') AND next_attempt_at IS NULL)",
            name=op.f("ck_source_tasks_next_attempt"),
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'terminal_failure', 'cancelled') AND "
            "finished_at IS NOT NULL AND finished_at >= created_at) OR (status NOT IN "
            "('succeeded', 'terminal_failure', 'cancelled') AND finished_at IS NULL)",
            name=op.f("ck_source_tasks_finished"),
        ),
        sa.CheckConstraint(
            "error_category IS NULL OR error_category NOT IN ('quota', 'throttle', "
            "'limit') OR status IN ('deferred', 'retryable_failure')",
            name=op.f("ck_source_tasks_limits_defer"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parameters) = 'object' AND octet_length(parameters::text) <= 16384",
            name=op.f("ck_source_tasks_compact_parameters"),
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR (result_artifact_uri IS NOT NULL AND "
            "result_artifact_uri LIKE 's3://%')",
            name=op.f("ck_source_tasks_success_evidence"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'running', 'deferred', "
            "'retryable_failure', 'succeeded', 'terminal_failure', 'cancelled')",
            name=op.f("ck_source_tasks_status"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('deferred', 'retryable_failure', 'terminal_failure') OR "
            "(error_category IS NOT NULL AND length(btrim(error_category)) > 0)",
            name=op.f("ck_source_tasks_failure_category"),
        ),
        sa.CheckConstraint(
            "task_kind IN ('search', 'search_page', 'vacancy_fetch', 'resume_sync', "
            "'resume_fetch')",
            name=op.f("ck_source_tasks_task_kind"),
        ),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_source_tasks_attempt_count")),
        sa.CheckConstraint("length(btrim(task_key)) > 0", name=op.f("ck_source_tasks_task_key")),
        sa.CheckConstraint(
            "parent_task_id IS NULL OR parent_task_id <> id",
            name=op.f("ck_source_tasks_parent_not_self"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["careerops_v2.accounts.id"],
            name=op.f("fk_source_tasks_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_task_id", "account_id"],
            ["careerops_v2.source_tasks.id", "careerops_v2.source_tasks.account_id"],
            name=op.f("fk_source_tasks_parent_task_id_source_tasks"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_tasks")),
        sa.UniqueConstraint(
            "account_id", "task_key", name=op.f("uq_source_tasks_account_id_task_key")
        ),
        sa.UniqueConstraint("id", "account_id", name=op.f("uq_source_tasks_id_account_id")),
        schema="careerops_v2",
    )
    op.create_index(
        "ix_source_tasks_expired_leases",
        "source_tasks",
        ["lease_expires_at"],
        unique=False,
        schema="careerops_v2",
        postgresql_where=sa.text("status IN ('claimed', 'running')"),
    )
    op.create_index(
        "ix_source_tasks_parent",
        "source_tasks",
        ["parent_task_id"],
        unique=False,
        schema="careerops_v2",
    )
    op.create_index(
        "ix_source_tasks_ready",
        "source_tasks",
        ["account_id", "next_attempt_at", "id"],
        unique=False,
        schema="careerops_v2",
        postgresql_where=sa.text("status IN ('pending', 'deferred', 'retryable_failure')"),
    )
    op.create_table(
        "vacancies",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("source_vacancy_id", sa.Text(), nullable=False),
        sa.Column("employer_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("remote", sa.Boolean(), nullable=True),
        sa.Column("employment_type", sa.Text(), nullable=True),
        sa.Column("experience", sa.Text(), nullable=True),
        sa.Column(
            "skill_keys",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("salary_from", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("salary_to", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("salary_currency", sa.Text(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=True),
        sa.Column("closed_for_applicants", sa.Boolean(), nullable=True),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "materialization_state",
            sa.Text(),
            server_default=sa.text("'identity_only'"),
            nullable=False,
        ),
        sa.Column("observed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("raw_uri", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("normalization_version", sa.Text(), nullable=True),
        sa.Column("materialization_key", sa.Text(), nullable=True),
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
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_vacancies_content_hash"),
        ),
        sa.CheckConstraint(
            "materialization_state <> 'current' OR (observed_at IS NOT NULL AND "
            "raw_uri IS NOT NULL AND raw_uri LIKE 's3://%' AND content_hash IS NOT "
            "NULL AND normalization_version IS NOT NULL AND "
            "length(btrim(normalization_version)) > 0 AND materialization_key IS NOT "
            "NULL AND length(btrim(materialization_key)) > 0)",
            name=op.f("ck_vacancies_current_provenance"),
        ),
        sa.CheckConstraint(
            "materialization_state IN ('identity_only', 'current', 'unavailable')",
            name=op.f("ck_vacancies_materialization_state"),
        ),
        sa.CheckConstraint(
            "(salary_from IS NULL OR salary_from >= 0) AND (salary_to IS NULL OR "
            "salary_to >= 0) AND (salary_from IS NULL OR salary_to IS NULL OR "
            "salary_to >= salary_from)",
            name=op.f("ck_vacancies_salary_range"),
        ),
        sa.CheckConstraint(
            "length(btrim(source_vacancy_id)) > 0", name=op.f("ck_vacancies_external_identity")
        ),
        sa.ForeignKeyConstraint(
            ["employer_id", "source_id"],
            ["careerops_v2.employers.id", "careerops_v2.employers.source_id"],
            name=op.f("fk_vacancies_employer_id_employers"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["careerops_v2.sources.id"], name=op.f("fk_vacancies_source_id_sources")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vacancies")),
        sa.UniqueConstraint(
            "source_id", "source_vacancy_id", name=op.f("uq_vacancies_source_id_source_vacancy_id")
        ),
        schema="careerops_v2",
    )
    op.create_index(
        "ix_vacancies_employer", "vacancies", ["employer_id"], unique=False, schema="careerops_v2"
    )
    op.create_table(
        "resumes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("profile_id", sa.BigInteger(), nullable=False),
        sa.Column("source_resume_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column(
            "skill_keys",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("upstream_status", sa.Text(), nullable=True),
        sa.Column("lifecycle", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("present_in_upstream", sa.Boolean(), nullable=True),
        sa.Column("inactive_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "materialization_state",
            sa.Text(),
            server_default=sa.text("'identity_only'"),
            nullable=False,
        ),
        sa.Column("observed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("raw_uri", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("normalization_version", sa.Text(), nullable=True),
        sa.Column("materialization_key", sa.Text(), nullable=True),
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
            "(lifecycle = 'unknown' AND present_in_upstream IS NULL AND inactive_at "
            "IS NULL) OR (lifecycle = 'active' AND present_in_upstream IS TRUE AND "
            "inactive_at IS NULL) OR (lifecycle = 'deleted' AND present_in_upstream "
            "IS FALSE AND inactive_at IS NOT NULL)",
            name=op.f("ck_resumes_lifecycle"),
        ),
        sa.CheckConstraint(
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_resumes_content_hash"),
        ),
        sa.CheckConstraint(
            "materialization_state <> 'current' OR (observed_at IS NOT NULL AND "
            "raw_uri IS NOT NULL AND raw_uri LIKE 's3://%' AND content_hash IS NOT "
            "NULL AND normalization_version IS NOT NULL AND "
            "length(btrim(normalization_version)) > 0 AND materialization_key IS NOT "
            "NULL AND length(btrim(materialization_key)) > 0)",
            name=op.f("ck_resumes_current_provenance"),
        ),
        sa.CheckConstraint(
            "materialization_state IN ('identity_only', 'current', 'unavailable')",
            name=op.f("ck_resumes_materialization_state"),
        ),
        sa.CheckConstraint(
            "length(btrim(source_resume_id)) > 0", name=op.f("ck_resumes_external_identity")
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "account_id", "source_id"],
            [
                "careerops_v2.profiles.id",
                "careerops_v2.profiles.account_id",
                "careerops_v2.profiles.source_id",
            ],
            name=op.f("fk_resumes_profile_id_profiles"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resumes")),
        sa.UniqueConstraint(
            "account_id", "source_resume_id", name=op.f("uq_resumes_account_id_source_resume_id")
        ),
        sa.UniqueConstraint("id", "account_id", name=op.f("uq_resumes_id_account_id")),
        schema="careerops_v2",
    )
    op.create_index(
        "ix_resumes_profile", "resumes", ["profile_id"], unique=False, schema="careerops_v2"
    )
    op.create_table(
        "resume_bindings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("resume_id", sa.BigInteger(), nullable=False),
        sa.Column("binding_key", sa.Text(), nullable=False),
        sa.Column("binding_version", sa.Integer(), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("auto_apply", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "query_set_keys",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
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
            "NOT auto_apply OR enabled", name=op.f("ck_resume_bindings_auto_apply_requires_enabled")
        ),
        sa.CheckConstraint("binding_version >= 1", name=op.f("ck_resume_bindings_binding_version")),
        sa.CheckConstraint(
            "length(btrim(binding_key)) > 0 AND length(btrim(target_key)) > 0",
            name=op.f("ck_resume_bindings_binding_keys"),
        ),
        sa.ForeignKeyConstraint(
            ["resume_id", "account_id"],
            ["careerops_v2.resumes.id", "careerops_v2.resumes.account_id"],
            name=op.f("fk_resume_bindings_resume_id_resumes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resume_bindings")),
        sa.UniqueConstraint(
            "account_id", "binding_key", name=op.f("uq_resume_bindings_account_id_binding_key")
        ),
        sa.UniqueConstraint("resume_id", name=op.f("uq_resume_bindings_resume_id")),
        schema="careerops_v2",
    )
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("binding_id", sa.BigInteger(), nullable=False),
        sa.Column("binding_version", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("input_manifest_uri", sa.Text(), nullable=False),
        sa.Column("pipeline_version", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_token", sa.UUID(), nullable=True),
        sa.Column("leased_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("lease_expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_category", sa.Text(), nullable=True),
        sa.Column("result_artifact_uri", sa.Text(), nullable=True),
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
            "(status IN ('claimed', 'running') AND lease_owner IS NOT NULL AND "
            "length(btrim(lease_owner)) > 0 AND lease_token IS NOT NULL AND leased_at "
            "IS NOT NULL AND lease_expires_at IS NOT NULL AND lease_expires_at > "
            "leased_at AND attempt_count > 0) OR (status NOT IN ('claimed', "
            "'running') AND lease_owner IS NULL AND lease_token IS NULL AND leased_at "
            "IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_processing_jobs_lease"),
        ),
        sa.CheckConstraint(
            "(status IN ('pending', 'deferred', 'retryable_failure') AND "
            "next_attempt_at IS NOT NULL) OR (status NOT IN ('pending', 'deferred', "
            "'retryable_failure') AND next_attempt_at IS NULL)",
            name=op.f("ck_processing_jobs_next_attempt"),
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'terminal_failure', 'cancelled') AND "
            "finished_at IS NOT NULL AND finished_at >= created_at) OR (status NOT IN "
            "('succeeded', 'terminal_failure', 'cancelled') AND finished_at IS NULL)",
            name=op.f("ck_processing_jobs_finished"),
        ),
        sa.CheckConstraint(
            "error_category IS NULL OR error_category NOT IN ('quota', 'throttle', "
            "'limit') OR status IN ('deferred', 'retryable_failure')",
            name=op.f("ck_processing_jobs_limits_defer"),
        ),
        sa.CheckConstraint(
            "input_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_processing_jobs_input_fingerprint"),
        ),
        sa.CheckConstraint(
            "input_manifest_uri LIKE 's3://%'", name=op.f("ck_processing_jobs_input_manifest_uri")
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR (result_artifact_uri IS NOT NULL AND "
            "result_artifact_uri LIKE 's3://%')",
            name=op.f("ck_processing_jobs_success_evidence"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'running', 'deferred', "
            "'retryable_failure', 'succeeded', 'terminal_failure', 'cancelled')",
            name=op.f("ck_processing_jobs_status"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('deferred', 'retryable_failure', 'terminal_failure') OR "
            "(error_category IS NOT NULL AND length(btrim(error_category)) > 0)",
            name=op.f("ck_processing_jobs_failure_category"),
        ),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_processing_jobs_attempt_count")),
        sa.CheckConstraint("binding_version >= 1", name=op.f("ck_processing_jobs_binding_version")),
        sa.CheckConstraint(
            "length(btrim(pipeline_version)) > 0 AND length(btrim(policy_version)) > 0",
            name=op.f("ck_processing_jobs_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["careerops_v2.resume_bindings.id"],
            name=op.f("fk_processing_jobs_binding_id_resume_bindings"),
        ),
        sa.ForeignKeyConstraint(
            ["vacancy_id"],
            ["careerops_v2.vacancies.id"],
            name=op.f("fk_processing_jobs_vacancy_id_vacancies"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_jobs")),
        sa.UniqueConstraint(
            "id",
            "vacancy_id",
            "binding_id",
            name=op.f("uq_processing_jobs_id_vacancy_id_binding_id"),
        ),
        sa.UniqueConstraint(
            "vacancy_id",
            "binding_id",
            "binding_version",
            "input_fingerprint",
            "pipeline_version",
            "policy_version",
            name="uq_processing_jobs_work",
        ),
        schema="careerops_v2",
    )
    op.create_index(
        "ix_processing_jobs_binding",
        "processing_jobs",
        ["binding_id"],
        unique=False,
        schema="careerops_v2",
    )
    op.create_index(
        "ix_processing_jobs_expired_leases",
        "processing_jobs",
        ["lease_expires_at"],
        unique=False,
        schema="careerops_v2",
        postgresql_where=sa.text("status IN ('claimed', 'running')"),
    )
    op.create_index(
        "ix_processing_jobs_ready",
        "processing_jobs",
        ["next_attempt_at", "id"],
        unique=False,
        schema="careerops_v2",
        postgresql_where=sa.text("status IN ('pending', 'deferred', 'retryable_failure')"),
    )
    op.create_table(
        "application_candidates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("binding_id", sa.BigInteger(), nullable=False),
        sa.Column("processing_job_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'review'"), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
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
            "status IN ('eligible', 'review', 'withdrawn')",
            name=op.f("ck_application_candidates_status"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_application_candidates_expiry")
        ),
        sa.ForeignKeyConstraint(
            ["processing_job_id", "vacancy_id", "binding_id"],
            [
                "careerops_v2.processing_jobs.id",
                "careerops_v2.processing_jobs.vacancy_id",
                "careerops_v2.processing_jobs.binding_id",
            ],
            name=op.f("fk_application_candidates_processing_job_id_processing_jobs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_candidates")),
        sa.UniqueConstraint(
            "vacancy_id", "binding_id", name=op.f("uq_application_candidates_vacancy_id_binding_id")
        ),
        schema="careerops_v2",
    )
    op.create_index(
        "ix_application_candidates_binding",
        "application_candidates",
        ["binding_id"],
        unique=False,
        schema="careerops_v2",
    )
    op.create_index(
        "ix_application_candidates_eligible",
        "application_candidates",
        ["expires_at"],
        unique=False,
        schema="careerops_v2",
        postgresql_where=sa.text("status = 'eligible'"),
    )
    op.create_table(
        "match_results",
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("binding_id", sa.BigInteger(), nullable=False),
        sa.Column("processing_job_id", sa.UUID(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("deterministic_score", sa.Numeric(precision=7, scale=4), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("computed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
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
            "artifact_uri LIKE 's3://%'", name=op.f("ck_match_results_artifact_uri")
        ),
        sa.CheckConstraint(
            "decision IN ('eligible', 'rejected', 'review')", name=op.f("ck_match_results_decision")
        ),
        sa.CheckConstraint(
            "deterministic_score BETWEEN 0 AND 100", name=op.f("ck_match_results_score")
        ),
        sa.ForeignKeyConstraint(
            ["processing_job_id", "vacancy_id", "binding_id"],
            [
                "careerops_v2.processing_jobs.id",
                "careerops_v2.processing_jobs.vacancy_id",
                "careerops_v2.processing_jobs.binding_id",
            ],
            name=op.f("fk_match_results_processing_job_id_processing_jobs"),
        ),
        sa.PrimaryKeyConstraint("vacancy_id", "binding_id", name=op.f("pk_match_results")),
        schema="careerops_v2",
    )
    op.create_index(
        "ix_match_results_binding",
        "match_results",
        ["binding_id", "decision"],
        unique=False,
        schema="careerops_v2",
    )
    op.create_table(
        "applications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("source_vacancy_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("resume_id", sa.BigInteger(), nullable=True),
        sa.Column("candidate_id", sa.UUID(), nullable=True),
        sa.Column("processing_job_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'blocked'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_reconcile_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_token", sa.UUID(), nullable=True),
        sa.Column("leased_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("lease_expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("prechecked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("precheck_evidence_uri", sa.Text(), nullable=True),
        sa.Column("submitted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("confirmed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "state_changed_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("error_category", sa.Text(), nullable=True),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("upstream_evidence_uri", sa.Text(), nullable=True),
        sa.Column("audit_uri", sa.Text(), nullable=True),
        sa.Column(
            "imported_from_legacy", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("recovery_source", sa.Text(), nullable=True),
        sa.Column("recovery_record_key", sa.Text(), nullable=True),
        sa.Column("imported_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
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
            "(imported_from_legacy AND recovery_source IS NOT NULL AND "
            "length(btrim(recovery_source)) > 0 AND recovery_record_key IS NOT NULL "
            "AND length(btrim(recovery_record_key)) > 0 AND imported_at IS NOT NULL "
            "AND audit_uri IS NOT NULL AND audit_uri LIKE 's3://%') OR (NOT "
            "imported_from_legacy AND recovery_source IS NULL AND recovery_record_key "
            "IS NULL AND imported_at IS NULL)",
            name=op.f("ck_applications_recovery_provenance"),
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND leased_at IS NULL AND "
            "lease_expires_at IS NULL AND status NOT IN ('preparing', 'precheck', "
            "'submitting')) OR (lease_owner IS NOT NULL AND "
            "length(btrim(lease_owner)) > 0 AND lease_token IS NOT NULL AND leased_at "
            "IS NOT NULL AND lease_expires_at IS NOT NULL AND lease_expires_at > "
            "leased_at AND status IN ('preparing', 'precheck', 'submitting', "
            "'reconciliation_required'))",
            name=op.f("ck_applications_lease"),
        ),
        sa.CheckConstraint(
            "(status = 'submitted_confirmed' AND confirmed_at IS NOT NULL AND "
            "upstream_evidence_uri IS NOT NULL AND upstream_evidence_uri LIKE "
            "'s3://%') OR (status <> 'submitted_confirmed' AND confirmed_at IS NULL)",
            name=op.f("ck_applications_confirmation"),
        ),
        sa.CheckConstraint(
            "(status IN ('submitted_unconfirmed', 'uncertain', "
            "'reconciliation_required') AND next_reconcile_at IS NOT NULL) OR (status "
            "NOT IN ('submitted_unconfirmed', 'uncertain', 'reconciliation_required') "
            "AND next_reconcile_at IS NULL)",
            name=op.f("ck_applications_reconcile_schedule"),
        ),
        sa.CheckConstraint(
            "next_attempt_at IS NULL OR status = 'safe_failure'",
            name=op.f("ck_applications_retry_safe_only"),
        ),
        sa.CheckConstraint(
            "status <> 'submitting' OR (prechecked_at IS NOT NULL AND "
            "precheck_evidence_uri IS NOT NULL AND precheck_evidence_uri LIKE "
            "'s3://%' AND audit_uri IS NOT NULL AND audit_uri LIKE 's3://%')",
            name=op.f("ck_applications_precheck_before_submission"),
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'preparing', 'precheck', 'submitting', "
            "'submitted_confirmed', 'submitted_unconfirmed', 'uncertain', "
            "'safe_failure', 'blocked', 'reconciliation_required')",
            name=op.f("ck_applications_status"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('candidate', 'preparing', 'precheck', 'submitting') OR "
            "(candidate_id IS NOT NULL AND resume_id IS NOT NULL "
            "AND processing_job_id IS NOT NULL)",
            name=op.f("ck_applications_live_identity"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('preparing', 'precheck', 'submitting') OR attempt_count > 0",
            name=op.f("ck_applications_live_attempt"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('safe_failure', 'blocked', 'uncertain', "
            "'reconciliation_required') OR (reason_code IS NOT NULL AND "
            "length(btrim(reason_code)) > 0)",
            name=op.f("ck_applications_reason"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('submitted_confirmed', 'submitted_unconfirmed') OR "
            "(submitted_at IS NOT NULL AND audit_uri IS NOT NULL AND audit_uri LIKE "
            "'s3://%')",
            name=op.f("ck_applications_submission_evidence"),
        ),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_applications_attempt_count")),
        sa.CheckConstraint(
            "confirmed_at IS NULL OR (submitted_at IS NOT NULL AND confirmed_at >= submitted_at)",
            name=op.f("ck_applications_confirmation_time"),
        ),
        sa.CheckConstraint(
            "length(btrim(source_vacancy_id)) > 0 AND length(btrim(idempotency_key)) > 0",
            name=op.f("ck_applications_keys"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "source_vacancy_id"],
            [
                "careerops_v2.application_guards.account_id",
                "careerops_v2.application_guards.source_vacancy_id",
            ],
            name="fk_applications_guard",
            initially="DEFERRED",
            deferrable=True,
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["careerops_v2.accounts.id"],
            name=op.f("fk_applications_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["careerops_v2.application_candidates.id"],
            name=op.f("fk_applications_candidate_id_application_candidates"),
        ),
        sa.ForeignKeyConstraint(
            ["processing_job_id"],
            ["careerops_v2.processing_jobs.id"],
            name=op.f("fk_applications_processing_job_id_processing_jobs"),
        ),
        sa.ForeignKeyConstraint(
            ["resume_id", "account_id"],
            ["careerops_v2.resumes.id", "careerops_v2.resumes.account_id"],
            name=op.f("fk_applications_resume_id_resumes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applications")),
        sa.UniqueConstraint(
            "account_id", "idempotency_key", name=op.f("uq_applications_account_id_idempotency_key")
        ),
        sa.UniqueConstraint(
            "id",
            "account_id",
            "source_vacancy_id",
            name=op.f("uq_applications_id_account_id_source_vacancy_id"),
        ),
        sa.UniqueConstraint(
            "recovery_source",
            "recovery_record_key",
            name=op.f("uq_applications_recovery_source_recovery_record_key"),
        ),
        schema="careerops_v2",
    )
    op.create_index(
        "ix_applications_processing_job",
        "applications",
        ["processing_job_id"],
        unique=False,
        schema="careerops_v2",
    )
    op.create_index(
        "ix_applications_candidate",
        "applications",
        ["candidate_id"],
        unique=False,
        schema="careerops_v2",
    )
    op.create_index(
        "ix_applications_expired_leases",
        "applications",
        ["lease_expires_at"],
        unique=False,
        schema="careerops_v2",
        postgresql_where=sa.text("lease_expires_at IS NOT NULL"),
    )
    op.create_index(
        "ix_applications_reconciliation",
        "applications",
        ["next_reconcile_at"],
        unique=False,
        schema="careerops_v2",
        postgresql_where=sa.text(
            "status IN ('submitted_unconfirmed', 'uncertain', 'reconciliation_required')"
        ),
    )
    op.create_index(
        "ix_applications_resume", "applications", ["resume_id"], unique=False, schema="careerops_v2"
    )
    op.create_index(
        "ix_applications_safe_retry",
        "applications",
        ["next_attempt_at"],
        unique=False,
        schema="careerops_v2",
        postgresql_where=sa.text("status = 'safe_failure' AND next_attempt_at IS NOT NULL"),
    )
    op.create_table(
        "application_guards",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("source_vacancy_id", sa.Text(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column(
            "scope_policy",
            sa.Text(),
            server_default=sa.text("'account_vacancy_v1'"),
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
            "scope_policy = 'account_vacancy_v1'", name=op.f("ck_application_guards_scope_policy")
        ),
        sa.CheckConstraint(
            "length(btrim(source_vacancy_id)) > 0",
            name=op.f("ck_application_guards_external_identity"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["careerops_v2.accounts.id"],
            name=op.f("fk_application_guards_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "account_id", "source_vacancy_id"],
            [
                "careerops_v2.applications.id",
                "careerops_v2.applications.account_id",
                "careerops_v2.applications.source_vacancy_id",
            ],
            name="fk_application_guards_application",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint(
            "account_id", "source_vacancy_id", name=op.f("pk_application_guards")
        ),
        schema="careerops_v2",
    )
    op.create_index(
        "ix_application_guards_application",
        "application_guards",
        ["application_id"],
        unique=False,
        schema="careerops_v2",
    )
    op.create_foreign_key(
        "fk_applications_guard",
        "applications",
        "application_guards",
        ["account_id", "source_vacancy_id"],
        ["account_id", "source_vacancy_id"],
        source_schema="careerops_v2",
        referent_schema="careerops_v2",
        initially="DEFERRED",
        deferrable=True,
        use_alter=True,
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_applications_guard", "applications", schema="careerops_v2", type_="foreignkey"
    )
    op.drop_table("application_guards", schema="careerops_v2")
    op.drop_table("applications", schema="careerops_v2")
    op.drop_table("match_results", schema="careerops_v2")
    op.drop_table("application_candidates", schema="careerops_v2")
    op.drop_table("processing_jobs", schema="careerops_v2")
    op.drop_table("resume_bindings", schema="careerops_v2")
    op.drop_table("resumes", schema="careerops_v2")
    op.drop_table("vacancies", schema="careerops_v2")
    op.drop_table("source_tasks", schema="careerops_v2")
    op.drop_table("profiles", schema="careerops_v2")
    op.drop_table("employers", schema="careerops_v2")
    op.drop_table("accounts", schema="careerops_v2")
    op.drop_table("sources", schema="careerops_v2")
    op.execute(sa.schema.DropSchema("careerops_v2"))
