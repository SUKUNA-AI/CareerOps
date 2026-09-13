"""Гарантирует не более одного активного Processing job на vacancy × binding"""

import sqlalchemy as sa

from alembic import context, op

revision = "20260907_v2_0003"
down_revision = "20260906_v2_0002"
branch_labels = None
depends_on = None

_ACTIVE_PROCESSING_JOB_SQL = (
    "status IN ('pending', 'claimed', 'running', 'deferred', 'retryable_failure')"
)
_ACTIVE_PROCESSING_JOB_PREDICATE = sa.text(_ACTIVE_PROCESSING_JOB_SQL)


def _duplicate_active_pairs() -> list[tuple[int, int, int]]:
    rows = op.get_bind().execute(
        sa.text(
            f"""
            SELECT vacancy_id, binding_id, count(*) AS active_count
            FROM careerops_v2.processing_jobs
            WHERE {_ACTIVE_PROCESSING_JOB_SQL}
            GROUP BY vacancy_id, binding_id
            HAVING count(*) > 1
            ORDER BY vacancy_id, binding_id
            LIMIT 20
            """
        )
    )
    return [(int(row[0]), int(row[1]), int(row[2])) for row in rows]


def upgrade() -> None:
    if not context.is_offline_mode():
        duplicates = _duplicate_active_pairs()
        if duplicates:
            details = ", ".join(
                f"vacancy_id={vacancy_id}/binding_id={binding_id}/active_count={active_count}"
                for vacancy_id, binding_id, active_count in duplicates
            )
            raise RuntimeError(
                "cannot enforce one active Processing job per pair: duplicate active Processing "
                f"job pairs exist before migration: {details}"
            )

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
