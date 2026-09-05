"""Application-owner current state and a conservative, non-expiring submission guard."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

from .domain import accounts, resumes
from .metadata import metadata, timestamps
from .processing import application_candidates, processing_jobs

applications = Table(
    "applications",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("account_id", BigInteger, ForeignKey(accounts.c.id), nullable=False),
    Column("source_vacancy_id", Text, nullable=False),
    Column("idempotency_key", Text, nullable=False),
    Column("resume_id", BigInteger),
    Column("candidate_id", UUID(as_uuid=True), ForeignKey(application_candidates.c.id)),
    Column("processing_job_id", UUID(as_uuid=True), ForeignKey(processing_jobs.c.id)),
    Column("status", Text, nullable=False, server_default=text("'blocked'")),
    Column("attempt_count", Integer, nullable=False, server_default=text("0")),
    Column("next_attempt_at", TIMESTAMP(timezone=True)),
    Column("next_reconcile_at", TIMESTAMP(timezone=True)),
    Column("lease_owner", Text),
    Column("lease_token", UUID(as_uuid=True)),
    Column("leased_at", TIMESTAMP(timezone=True)),
    Column("lease_expires_at", TIMESTAMP(timezone=True)),
    Column("prechecked_at", TIMESTAMP(timezone=True)),
    Column("precheck_evidence_uri", Text),
    Column("submitted_at", TIMESTAMP(timezone=True)),
    Column("confirmed_at", TIMESTAMP(timezone=True)),
    Column(
        "state_changed_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    ),
    Column("error_category", Text),
    Column("reason_code", Text),
    Column("upstream_evidence_uri", Text),
    Column("audit_uri", Text),
    Column("imported_from_legacy", Boolean, nullable=False, server_default=text("false")),
    Column("recovery_source", Text),
    Column("recovery_record_key", Text),
    Column("imported_at", TIMESTAMP(timezone=True)),
    *timestamps(),
    UniqueConstraint("account_id", "idempotency_key"),
    UniqueConstraint("id", "account_id", "source_vacancy_id"),
    UniqueConstraint("recovery_source", "recovery_record_key"),
    ForeignKeyConstraint(["resume_id", "account_id"], [resumes.c.id, resumes.c.account_id]),
    ForeignKeyConstraint(
        ["account_id", "source_vacancy_id"],
        [
            "careerops_v2.application_guards.account_id",
            "careerops_v2.application_guards.source_vacancy_id",
        ],
        name="fk_applications_guard",
        deferrable=True,
        initially="DEFERRED",
        use_alter=True,
    ),
    CheckConstraint(
        "length(btrim(source_vacancy_id)) > 0 AND length(btrim(idempotency_key)) > 0",
        name="keys",
    ),
    CheckConstraint(
        "status IN ('candidate', 'preparing', 'precheck', 'submitting', 'submitted_confirmed', "
        "'submitted_unconfirmed', 'uncertain', 'safe_failure', 'blocked', "
        "'reconciliation_required')",
        name="status",
    ),
    CheckConstraint("attempt_count >= 0", name="attempt_count"),
    CheckConstraint(
        "(lease_owner IS NULL AND lease_token IS NULL AND leased_at IS NULL "
        "AND lease_expires_at IS NULL AND status NOT IN ('preparing', 'precheck', 'submitting')) "
        "OR (lease_owner IS NOT NULL AND length(btrim(lease_owner)) > 0 "
        "AND lease_token IS NOT NULL AND leased_at IS NOT NULL AND lease_expires_at IS NOT NULL "
        "AND lease_expires_at > leased_at "
        "AND status IN ('preparing', 'precheck', 'submitting', 'reconciliation_required'))",
        name="lease",
    ),
    CheckConstraint(
        "status NOT IN ('candidate', 'preparing', 'precheck', 'submitting') "
        "OR (candidate_id IS NOT NULL AND resume_id IS NOT NULL AND processing_job_id IS NOT NULL)",
        name="live_identity",
    ),
    CheckConstraint(
        "status NOT IN ('preparing', 'precheck', 'submitting') OR attempt_count > 0",
        name="live_attempt",
    ),
    CheckConstraint(
        "status <> 'submitting' OR (prechecked_at IS NOT NULL "
        "AND precheck_evidence_uri IS NOT NULL AND precheck_evidence_uri LIKE 's3://%' "
        "AND audit_uri IS NOT NULL AND audit_uri LIKE 's3://%')",
        name="precheck_before_submission",
    ),
    CheckConstraint(
        "(status = 'submitted_confirmed' AND confirmed_at IS NOT NULL "
        "AND upstream_evidence_uri IS NOT NULL AND upstream_evidence_uri LIKE 's3://%') "
        "OR (status <> 'submitted_confirmed' AND confirmed_at IS NULL)",
        name="confirmation",
    ),
    CheckConstraint(
        "status NOT IN ('submitted_confirmed', 'submitted_unconfirmed') "
        "OR (submitted_at IS NOT NULL AND audit_uri IS NOT NULL AND audit_uri LIKE 's3://%')",
        name="submission_evidence",
    ),
    CheckConstraint(
        "confirmed_at IS NULL OR (submitted_at IS NOT NULL AND confirmed_at >= submitted_at)",
        name="confirmation_time",
    ),
    CheckConstraint("next_attempt_at IS NULL OR status = 'safe_failure'", name="retry_safe_only"),
    CheckConstraint(
        "(status IN ('submitted_unconfirmed', 'uncertain', 'reconciliation_required') "
        "AND next_reconcile_at IS NOT NULL) OR "
        "(status NOT IN ('submitted_unconfirmed', 'uncertain', 'reconciliation_required') "
        "AND next_reconcile_at IS NULL)",
        name="reconcile_schedule",
    ),
    CheckConstraint(
        "status NOT IN ('safe_failure', 'blocked', 'uncertain', 'reconciliation_required') "
        "OR (reason_code IS NOT NULL AND length(btrim(reason_code)) > 0)",
        name="reason",
    ),
    CheckConstraint(
        "(imported_from_legacy AND recovery_source IS NOT NULL "
        "AND length(btrim(recovery_source)) > 0 AND recovery_record_key IS NOT NULL "
        "AND length(btrim(recovery_record_key)) > 0 AND imported_at IS NOT NULL "
        "AND audit_uri IS NOT NULL AND audit_uri LIKE 's3://%') OR "
        "(NOT imported_from_legacy AND recovery_source IS NULL "
        "AND recovery_record_key IS NULL AND imported_at IS NULL)",
        name="recovery_provenance",
    ),
)
Index("ix_applications_resume", applications.c.resume_id)
Index("ix_applications_candidate", applications.c.candidate_id)
Index("ix_applications_processing_job", applications.c.processing_job_id)
Index(
    "ix_applications_reconciliation",
    applications.c.next_reconcile_at,
    postgresql_where=text(
        "status IN ('submitted_unconfirmed', 'uncertain', 'reconciliation_required')"
    ),
)
Index(
    "ix_applications_expired_leases",
    applications.c.lease_expires_at,
    postgresql_where=text("lease_expires_at IS NOT NULL"),
)
Index(
    "ix_applications_safe_retry",
    applications.c.next_attempt_at,
    postgresql_where=text("status = 'safe_failure' AND next_attempt_at IS NOT NULL"),
)

application_guards = Table(
    "application_guards",
    metadata,
    Column("account_id", BigInteger, ForeignKey(accounts.c.id), primary_key=True),
    Column("source_vacancy_id", Text, primary_key=True),
    Column("application_id", UUID(as_uuid=True), nullable=False),
    Column("scope_policy", Text, nullable=False, server_default=text("'account_vacancy_v1'")),
    *timestamps(),
    ForeignKeyConstraint(
        ["application_id", "account_id", "source_vacancy_id"],
        [applications.c.id, applications.c.account_id, applications.c.source_vacancy_id],
        name="fk_application_guards_application",
        deferrable=True,
        initially="DEFERRED",
    ),
    CheckConstraint("length(btrim(source_vacancy_id)) > 0", name="external_identity"),
    CheckConstraint("scope_policy = 'account_vacancy_v1'", name="scope_policy"),
)
Index("ix_application_guards_application", application_guards.c.application_id)
