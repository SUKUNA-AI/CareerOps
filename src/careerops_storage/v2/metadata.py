"""Общие naming, timestamps и lease-структуры PostgreSQL v2"""

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Identity,
    Integer,
    MetaData,
    Text,
    text,
)
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
    """Возвращает общие operational columns durable queue"""

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
    """Возвращает общие invariants durable queue"""

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
    """Общие поля current materialization без Processing-specific normalized ref"""

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


def normalized_ref_provenance() -> list[Column[Any] | CheckConstraint]:
    """Поля, из которых PostgreSQL может восстановить точный NormalizedRef Processing"""

    return [
        Column("raw_sha256", Text),
        Column("normalized_uri", Text),
        Column("normalized_sha256", Text),
        Column("semantic_content_hash", Text),
        Column("normalized_schema_version", Text),
        Column("dictionary_version", Text),
        Column("dq_status", Text),
        Column("processing_ready", Boolean),
        CheckConstraint(
            "raw_sha256 IS NULL OR raw_sha256 ~ '^[0-9a-f]{64}$'",
            name="normalized_raw_sha256",
        ),
        CheckConstraint(
            "normalized_sha256 IS NULL OR normalized_sha256 ~ '^[0-9a-f]{64}$'",
            name="normalized_sha256",
        ),
        CheckConstraint(
            "semantic_content_hash IS NULL OR semantic_content_hash ~ '^[0-9a-f]{64}$'",
            name="semantic_content_hash",
        ),
        CheckConstraint(
            "normalized_uri IS NULL OR normalized_uri LIKE 's3://%'",
            name="normalized_uri",
        ),
        CheckConstraint(
            "dq_status IS NULL OR dq_status IN ('clean', 'warning', 'blocked')",
            name="dq_status",
        ),
        CheckConstraint(
            "(raw_sha256 IS NULL AND normalized_uri IS NULL AND normalized_sha256 IS NULL "
            "AND semantic_content_hash IS NULL AND normalized_schema_version IS NULL "
            "AND dictionary_version IS NULL AND dq_status IS NULL AND processing_ready IS NULL) "
            "OR (raw_sha256 IS NOT NULL AND normalized_uri IS NOT NULL "
            "AND normalized_sha256 IS NOT NULL AND semantic_content_hash IS NOT NULL "
            "AND normalized_schema_version IS NOT NULL "
            "AND length(btrim(normalized_schema_version)) > 0 "
            "AND dictionary_version IS NOT NULL AND length(btrim(dictionary_version)) > 0 "
            "AND dq_status IS NOT NULL AND processing_ready IS NOT NULL)",
            name="normalized_ref_complete",
        ),
        CheckConstraint(
            "normalized_uri IS NULL OR materialization_state = 'current'",
            name="normalized_ref_requires_current",
        ),
        CheckConstraint(
            "processing_ready IS DISTINCT FROM TRUE OR dq_status <> 'blocked'",
            name="processing_ready_not_blocked",
        ),
    ]
