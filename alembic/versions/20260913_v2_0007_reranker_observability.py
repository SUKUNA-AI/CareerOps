"""Add operational reranker audit index.

Revision ID: 20260913_v2_0007
Revises: 20260910_v2_0006
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260913_v2_0007"
down_revision = "20260910_v2_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reranker_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("processing_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("binding_id", sa.BigInteger(), nullable=False),
        sa.Column("requirement_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("pool_size", sa.Integer(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("selected_size", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("latency_ms", sa.BigInteger(), nullable=False),
        sa.Column("runtime_fingerprint", sa.Text()),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("error_class", sa.Text()),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["processing_job_id", "vacancy_id", "binding_id"],
            [
                "careerops_v2.processing_jobs.id",
                "careerops_v2.processing_jobs.vacancy_id",
                "careerops_v2.processing_jobs.binding_id",
            ],
            name=op.f("fk_reranker_runs_processing_job_id_processing_jobs"),
        ),
        sa.CheckConstraint(
            "status IN ('success', 'error')",
            name=op.f("ck_reranker_runs_status"),
        ),
        sa.CheckConstraint(
            "length(btrim(requirement_id)) > 0",
            name=op.f("ck_reranker_runs_requirement_id"),
        ),
        sa.CheckConstraint("pool_size > 0", name=op.f("ck_reranker_runs_pool_size")),
        sa.CheckConstraint(
            "top_k > 0 AND top_k <= pool_size",
            name=op.f("ck_reranker_runs_top_k"),
        ),
        sa.CheckConstraint(
            "selected_size >= 0 AND selected_size <= top_k",
            name=op.f("ck_reranker_runs_selected_size"),
        ),
        sa.CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name=op.f("ck_reranker_runs_total_tokens"),
        ),
        sa.CheckConstraint("latency_ms >= 0", name=op.f("ck_reranker_runs_latency_ms")),
        sa.CheckConstraint(
            "runtime_fingerprint IS NULL OR runtime_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_reranker_runs_runtime_fingerprint"),
        ),
        sa.CheckConstraint(
            "artifact_uri LIKE 's3://%'",
            name=op.f("ck_reranker_runs_artifact_uri"),
        ),
        sa.CheckConstraint(
            "finished_at >= started_at",
            name=op.f("ck_reranker_runs_timestamps"),
        ),
        sa.CheckConstraint(
            "(status = 'success' AND error_class IS NULL) OR "
            "(status = 'error' AND error_class IS NOT NULL "
            "AND length(btrim(error_class)) > 0)",
            name=op.f("ck_reranker_runs_error_class"),
        ),
        schema="careerops_v2",
    )
    op.create_index(
        "ix_reranker_runs_job",
        "reranker_runs",
        ["processing_job_id", "started_at"],
        schema="careerops_v2",
    )
    op.create_index(
        "ix_reranker_runs_pair",
        "reranker_runs",
        ["vacancy_id", "binding_id", "started_at"],
        schema="careerops_v2",
    )
    op.create_index(
        "ix_reranker_runs_status",
        "reranker_runs",
        ["status", "created_at"],
        schema="careerops_v2",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reranker_runs_status",
        table_name="reranker_runs",
        schema="careerops_v2",
    )
    op.drop_index(
        "ix_reranker_runs_pair",
        table_name="reranker_runs",
        schema="careerops_v2",
    )
    op.drop_index(
        "ix_reranker_runs_job",
        table_name="reranker_runs",
        schema="careerops_v2",
    )
    op.drop_table("reranker_runs", schema="careerops_v2")
