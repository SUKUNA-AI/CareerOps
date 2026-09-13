"""Версионированная очередь Processing и текущее состояние решений."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP, UUID

from .domain import resume_bindings, vacancies
from .metadata import metadata, queue_columns, queue_constraints, timestamps

processing_jobs = Table(
    "processing_jobs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("vacancy_id", BigInteger, ForeignKey(vacancies.c.id), nullable=False),
    Column("binding_id", BigInteger, ForeignKey(resume_bindings.c.id), nullable=False),
    Column("binding_version", Integer, nullable=False),
    Column("input_fingerprint", Text, nullable=False),
    Column("input_manifest_uri", Text, nullable=False),
    Column("pipeline_version", Text, nullable=False),
    Column("policy_version", Text, nullable=False),
    *queue_columns(),
    UniqueConstraint(
        "vacancy_id",
        "binding_id",
        "binding_version",
        "input_fingerprint",
        "pipeline_version",
        "policy_version",
        name="uq_processing_jobs_work",
    ),
    UniqueConstraint("id", "vacancy_id", "binding_id"),
    CheckConstraint("binding_version >= 1", name="binding_version"),
    CheckConstraint("input_fingerprint ~ '^[0-9a-f]{64}$'", name="input_fingerprint"),
    CheckConstraint("input_manifest_uri LIKE 's3://%'", name="input_manifest_uri"),
    CheckConstraint(
        "length(btrim(pipeline_version)) > 0 AND length(btrim(policy_version)) > 0",
        name="versions",
    ),
    *queue_constraints(),
)
Index(
    "ix_processing_jobs_ready",
    processing_jobs.c.next_attempt_at,
    processing_jobs.c.id,
    postgresql_where=text("status IN ('pending', 'deferred', 'retryable_failure')"),
)
Index(
    "ix_processing_jobs_expired_leases",
    processing_jobs.c.lease_expires_at,
    postgresql_where=text("status IN ('claimed', 'running')"),
)
Index(
    "uq_processing_jobs_active_pair",
    processing_jobs.c.vacancy_id,
    processing_jobs.c.binding_id,
    unique=True,
    postgresql_where=text(
        "status IN ('pending', 'claimed', 'running', 'deferred', 'retryable_failure')"
    ),
)
Index("ix_processing_jobs_binding", processing_jobs.c.binding_id)

processing_semantic_artifacts = Table(
    "processing_semantic_artifacts",
    metadata,
    Column("cache_key", Text, primary_key=True),
    Column("artifact_kind", Text, nullable=False),
    Column("source_key", Text, nullable=False),
    Column("source_entity_id", Text, nullable=False),
    Column("account_key", Text),
    Column("semantic_content_hash", Text, nullable=False),
    Column("normalized_schema_version", Text, nullable=False),
    Column("normalization_version", Text, nullable=False),
    Column("dictionary_version", Text, nullable=False),
    Column("semantic_version", Text, nullable=False),
    Column("artifact_schema_version", Text, nullable=False),
    Column("artifact_uri", Text, nullable=False),
    Column("artifact_sha256", Text, nullable=False),
    Column("artifact_size_bytes", BigInteger, nullable=False),
    *timestamps(),
    CheckConstraint("cache_key ~ '^[0-9a-f]{64}$'", name="cache_key"),
    CheckConstraint(
        "artifact_kind IN ('requirement_set', 'resume_evidence_set')",
        name="artifact_kind",
    ),
    CheckConstraint("semantic_content_hash ~ '^[0-9a-f]{64}$'", name="semantic_content_hash"),
    CheckConstraint("artifact_sha256 ~ '^[0-9a-f]{64}$'", name="artifact_sha256"),
    CheckConstraint("artifact_uri LIKE 's3://%'", name="artifact_uri"),
    CheckConstraint("artifact_size_bytes > 0", name="artifact_size_bytes"),
    CheckConstraint(
        "length(btrim(source_key)) > 0 "
        "AND length(btrim(source_entity_id)) > 0 "
        "AND length(btrim(normalized_schema_version)) > 0 "
        "AND length(btrim(normalization_version)) > 0 "
        "AND length(btrim(dictionary_version)) > 0 "
        "AND length(btrim(semantic_version)) > 0 "
        "AND length(btrim(artifact_schema_version)) > 0",
        name="non_empty_identity",
    ),
    CheckConstraint(
        "(artifact_kind = 'requirement_set' AND account_key IS NULL) OR "
        "(artifact_kind = 'resume_evidence_set' AND account_key IS NOT NULL "
        "AND length(btrim(account_key)) > 0)",
        name="account_scope",
    ),
)
Index(
    "ix_processing_semantic_artifacts_source",
    processing_semantic_artifacts.c.artifact_kind,
    processing_semantic_artifacts.c.source_key,
    processing_semantic_artifacts.c.source_entity_id,
)

reranker_runs = Table(
    "reranker_runs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("processing_job_id", UUID(as_uuid=True), nullable=False),
    Column("vacancy_id", BigInteger, nullable=False),
    Column("binding_id", BigInteger, nullable=False),
    Column("requirement_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("pool_size", Integer, nullable=False),
    Column("top_k", Integer, nullable=False),
    Column("selected_size", Integer, nullable=False),
    Column("total_tokens", Integer),
    Column("latency_ms", BigInteger, nullable=False),
    Column("runtime_fingerprint", Text),
    Column("artifact_uri", Text, nullable=False),
    Column("error_class", Text),
    Column("started_at", TIMESTAMP(timezone=True), nullable=False),
    Column("finished_at", TIMESTAMP(timezone=True), nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    ForeignKeyConstraint(
        ["processing_job_id", "vacancy_id", "binding_id"],
        [processing_jobs.c.id, processing_jobs.c.vacancy_id, processing_jobs.c.binding_id],
    ),
    CheckConstraint("status IN ('success', 'error')", name="status"),
    CheckConstraint("length(btrim(requirement_id)) > 0", name="requirement_id"),
    CheckConstraint("pool_size > 0", name="pool_size"),
    CheckConstraint("top_k > 0 AND top_k <= pool_size", name="top_k"),
    CheckConstraint("selected_size >= 0 AND selected_size <= top_k", name="selected_size"),
    CheckConstraint("total_tokens IS NULL OR total_tokens >= 0", name="total_tokens"),
    CheckConstraint("latency_ms >= 0", name="latency_ms"),
    CheckConstraint(
        "runtime_fingerprint IS NULL OR runtime_fingerprint ~ '^[0-9a-f]{64}$'",
        name="runtime_fingerprint",
    ),
    CheckConstraint("artifact_uri LIKE 's3://%'", name="artifact_uri"),
    CheckConstraint("finished_at >= started_at", name="timestamps"),
    CheckConstraint(
        "(status = 'success' AND error_class IS NULL) OR "
        "(status = 'error' AND error_class IS NOT NULL AND length(btrim(error_class)) > 0)",
        name="error_class",
    ),
)
Index("ix_reranker_runs_job", reranker_runs.c.processing_job_id, reranker_runs.c.started_at)
Index(
    "ix_reranker_runs_pair",
    reranker_runs.c.vacancy_id,
    reranker_runs.c.binding_id,
    reranker_runs.c.started_at,
)
Index("ix_reranker_runs_status", reranker_runs.c.status, reranker_runs.c.created_at)

match_results = Table(
    "match_results",
    metadata,
    Column("vacancy_id", BigInteger, primary_key=True),
    Column("binding_id", BigInteger, primary_key=True),
    Column("processing_job_id", UUID(as_uuid=True), nullable=False),
    Column("decision", Text, nullable=False),
    Column("deterministic_score", Numeric(7, 4), nullable=False),
    Column("reason_codes", ARRAY(Text), nullable=False),
    Column("artifact_uri", Text, nullable=False),
    Column("computed_at", TIMESTAMP(timezone=True), nullable=False),
    *timestamps(),
    ForeignKeyConstraint(
        ["processing_job_id", "vacancy_id", "binding_id"],
        [processing_jobs.c.id, processing_jobs.c.vacancy_id, processing_jobs.c.binding_id],
    ),
    CheckConstraint("decision IN ('eligible', 'rejected', 'review')", name="decision"),
    CheckConstraint("deterministic_score BETWEEN 0 AND 100", name="score"),
    CheckConstraint("artifact_uri LIKE 's3://%'", name="artifact_uri"),
)
Index("ix_match_results_binding", match_results.c.binding_id, match_results.c.decision)

application_candidates = Table(
    "application_candidates",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("vacancy_id", BigInteger, nullable=False),
    Column("binding_id", BigInteger, nullable=False),
    Column("processing_job_id", UUID(as_uuid=True), nullable=False),
    Column("status", Text, nullable=False, server_default=text("'review'")),
    Column("expires_at", TIMESTAMP(timezone=True), nullable=False),
    *timestamps(),
    UniqueConstraint("vacancy_id", "binding_id"),
    ForeignKeyConstraint(
        ["processing_job_id", "vacancy_id", "binding_id"],
        [processing_jobs.c.id, processing_jobs.c.vacancy_id, processing_jobs.c.binding_id],
    ),
    CheckConstraint("status IN ('eligible', 'review', 'withdrawn')", name="status"),
    CheckConstraint("expires_at > created_at", name="expiry"),
)
Index(
    "ix_application_candidates_eligible",
    application_candidates.c.expires_at,
    postgresql_where=text("status = 'eligible'"),
)
Index("ix_application_candidates_binding", application_candidates.c.binding_id)
