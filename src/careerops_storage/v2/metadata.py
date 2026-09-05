"""Shared PostgreSQL v2 naming, timestamps and operational lease shape."""

from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Column, Identity, Integer, MetaData, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

SCHEMA = "careerops_v2"
metadata = MetaData(
    schema=SCHEMA,
    naming_convention={
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    },
)


def numeric_id() -> Column[int]:
    return Column("id", BigInteger, Identity(), primary_key=True)


def timestamps() -> list[Column[Any]]:
    return [
        Column(
            "created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
        ),
        Column(
            "updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
        ),
    ]


def queue_columns() -> list[Column[Any]]:
    return [
        Column("status", Text, nullable=False, server_default=text("'pending'")),
        Column("attempt_count", Integer, nullable=False, server_default=text("0")),
        Column("next_attempt_at", TIMESTAMP(timezone=True), server_default=text("now()")),
        Column("lease_owner", Text),
        Column("lease_token", UUID(as_uuid=True)),
        Column("leased_at", TIMESTAMP(timezone=True)),
        Column("lease_expires_at", TIMESTAMP(timezone=True)),
        Column("finished_at", TIMESTAMP(timezone=True)),
        Column("error_category", Text),
        Column("result_artifact_uri", Text),
        *timestamps(),
    ]


def queue_constraints() -> list[CheckConstraint]:
    return [
        CheckConstraint(
            "status IN ('pending', 'claimed', 'running', 'deferred', 'retryable_failure', "
            "'succeeded', 'terminal_failure', 'cancelled')",
            name="status",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count"),
        CheckConstraint(
            "(status IN ('claimed', 'running') AND lease_owner IS NOT NULL "
            "AND length(btrim(lease_owner)) > 0 AND lease_token IS NOT NULL "
            "AND leased_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at > leased_at AND attempt_count > 0) OR "
            "(status NOT IN ('claimed', 'running') AND lease_owner IS NULL "
            "AND lease_token IS NULL AND leased_at IS NULL AND lease_expires_at IS NULL)",
            name="lease",
        ),
        CheckConstraint(
            "(status IN ('pending', 'deferred', 'retryable_failure') "
            "AND next_attempt_at IS NOT NULL) OR "
            "(status NOT IN ('pending', 'deferred', 'retryable_failure') "
            "AND next_attempt_at IS NULL)",
            name="next_attempt",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'terminal_failure', 'cancelled') "
            "AND finished_at IS NOT NULL AND finished_at >= created_at) OR "
            "(status NOT IN ('succeeded', 'terminal_failure', 'cancelled') "
            "AND finished_at IS NULL)",
            name="finished",
        ),
        CheckConstraint(
            "status <> 'succeeded' OR (result_artifact_uri IS NOT NULL "
            "AND result_artifact_uri LIKE 's3://%')",
            name="success_evidence",
        ),
        CheckConstraint(
            "status NOT IN ('deferred', 'retryable_failure', 'terminal_failure') OR "
            "(error_category IS NOT NULL AND length(btrim(error_category)) > 0)",
            name="failure_category",
        ),
        CheckConstraint(
            "error_category IS NULL OR error_category NOT IN ('quota', 'throttle', 'limit') "
            "OR status IN ('deferred', 'retryable_failure')",
            name="limits_defer",
        ),
    ]


def current_provenance() -> list[Column[Any] | CheckConstraint]:
    return [
        Column(
            "materialization_state", Text, nullable=False, server_default=text("'identity_only'")
        ),
        Column("observed_at", TIMESTAMP(timezone=True)),
        Column("raw_uri", Text),
        Column("content_hash", Text),
        Column("normalization_version", Text),
        Column("materialization_key", Text),
        CheckConstraint(
            "materialization_state IN ('identity_only', 'current', 'unavailable')",
            name="materialization_state",
        ),
        CheckConstraint(
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'", name="content_hash"
        ),
        CheckConstraint(
            "materialization_state <> 'current' OR (observed_at IS NOT NULL "
            "AND raw_uri IS NOT NULL AND raw_uri LIKE 's3://%' AND content_hash IS NOT NULL "
            "AND normalization_version IS NOT NULL AND length(btrim(normalization_version)) > 0 "
            "AND materialization_key IS NOT NULL AND length(btrim(materialization_key)) > 0)",
            name="current_provenance",
        ),
    ]
