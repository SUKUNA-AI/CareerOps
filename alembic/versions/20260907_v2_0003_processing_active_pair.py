"""Enforce one active Processing job per vacancy × binding pair."""

import sqlalchemy as sa

from alembic import op

revision = "20260907_v2_0003"
down_revision = "20260906_v2_0002"
branch_labels = None
depends_on = None

_ACTIVE_PROCESSING_JOB_PREDICATE = sa.text(
    "status IN ('pending', 'claimed', 'running', 'deferred', 'retryable_failure')"
)


def upgrade() -> None:
    op.create_index(
        "uq_processing_jobs_active_pair",
        "processing_jobs",
        ["vacancy_id", "binding_id"],
        unique=True,
        schema="careerops_v2",
        postgresql_where=_ACTIVE_PROCESSING_JOB_PREDICATE,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_processing_jobs_active_pair",
        table_name="processing_jobs",
        schema="careerops_v2",
    )
