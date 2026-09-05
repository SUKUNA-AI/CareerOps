"""Persistent adapter work; limits preserve queued work by deferring it."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .domain import accounts
from .metadata import metadata, queue_columns, queue_constraints

source_tasks = Table(
    "source_tasks",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("account_id", BigInteger, ForeignKey(accounts.c.id), nullable=False),
    Column("task_key", Text, nullable=False),
    Column("task_kind", Text, nullable=False),
    Column("parent_task_id", UUID(as_uuid=True)),
    Column("parameters", JSONB, nullable=False),
    *queue_columns(),
    UniqueConstraint("account_id", "task_key"),
    UniqueConstraint("id", "account_id"),
    ForeignKeyConstraint(
        ["parent_task_id", "account_id"],
        ["careerops_v2.source_tasks.id", "careerops_v2.source_tasks.account_id"],
    ),
    CheckConstraint("length(btrim(task_key)) > 0", name="task_key"),
    CheckConstraint("parent_task_id IS NULL OR parent_task_id <> id", name="parent_not_self"),
    CheckConstraint(
        "task_kind IN ('search', 'search_page', 'vacancy_fetch', 'resume_sync', 'resume_fetch')",
        name="task_kind",
    ),
    CheckConstraint(
        "jsonb_typeof(parameters) = 'object' AND octet_length(parameters::text) <= 16384",
        name="compact_parameters",
    ),
    *queue_constraints(),
)
Index(
    "ix_source_tasks_ready",
    source_tasks.c.account_id,
    source_tasks.c.next_attempt_at,
    source_tasks.c.id,
    postgresql_where=text("status IN ('pending', 'deferred', 'retryable_failure')"),
)
Index(
    "ix_source_tasks_expired_leases",
    source_tasks.c.lease_expires_at,
    postgresql_where=text("status IN ('claimed', 'running')"),
)
Index("ix_source_tasks_parent", source_tasks.c.parent_task_id)
