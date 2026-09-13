"""Убирает устаревшую v2 schema и замыкает границу Spark → Processing для NormalizedRef"""

import sqlalchemy as sa

from alembic import context, op

revision = "20260908_v2_0004"
down_revision = "20260907_v2_0003"
branch_labels = None
depends_on = None

_NORMALIZED_TABLES = ("vacancies", "resumes")
_NORMALIZED_CONSTRAINTS = (
    "normalized_raw_sha256",
    "normalized_sha256",
    "semantic_content_hash",
    "normalized_uri",
    "dq_status",
    "normalized_ref_complete",
    "normalized_ref_requires_current",
    "processing_ready_not_blocked",
)
_NORMALIZED_COLUMNS = (
    "raw_sha256",
    "normalized_uri",
    "normalized_sha256",
    "semantic_content_hash",
    "normalized_schema_version",
    "dictionary_version",
    "dq_status",
    "processing_ready",
)


def _assert_no_dead_search_tasks() -> None:
    count = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM careerops_v2.source_tasks WHERE task_kind = 'search'"
        )
    ).scalar_one()
    if int(count) != 0:
        raise RuntimeError(
            "cannot remove retired source task kind 'search': rows still exist in "
            "careerops_v2.source_tasks"
        )


def _assert_no_application_recovery_rows() -> None:
    count = op.get_bind().execute(
        sa.text(
            """
            SELECT count(*)
            FROM careerops_v2.applications
            WHERE imported_from_legacy IS TRUE
               OR recovery_source IS NOT NULL
               OR recovery_record_key IS NOT NULL
               OR imported_at IS NOT NULL
            """
        )
    ).scalar_one()
    if int(count) != 0:
        raise RuntimeError(
            "cannot remove retired application recovery columns: recovery rows still exist"
        )


def _add_normalized_ref_columns(table_name: str) -> None:
    op.add_column(table_name, sa.Column("raw_sha256", sa.Text()), schema="careerops_v2")
    op.add_column(table_name, sa.Column("normalized_uri", sa.Text()), schema="careerops_v2")
    op.add_column(table_name, sa.Column("normalized_sha256", sa.Text()), schema="careerops_v2")
    op.add_column(
        table_name,
        sa.Column("semantic_content_hash", sa.Text()),
        schema="careerops_v2",
    )
    op.add_column(
        table_name,
        sa.Column("normalized_schema_version", sa.Text()),
        schema="careerops_v2",
    )
    op.add_column(table_name, sa.Column("dictionary_version", sa.Text()), schema="careerops_v2")
    op.add_column(table_name, sa.Column("dq_status", sa.Text()), schema="careerops_v2")
    op.add_column(table_name, sa.Column("processing_ready", sa.Boolean()), schema="careerops_v2")

    op.create_check_constraint(
        op.f(f"ck_{table_name}_normalized_raw_sha256"),
        table_name,
        "raw_sha256 IS NULL OR raw_sha256 ~ '^[0-9a-f]{64}$'",
        schema="careerops_v2",
    )
    op.create_check_constraint(
        op.f(f"ck_{table_name}_normalized_sha256"),
        table_name,
        "normalized_sha256 IS NULL OR normalized_sha256 ~ '^[0-9a-f]{64}$'",
        schema="careerops_v2",
    )
    op.create_check_constraint(
        op.f(f"ck_{table_name}_semantic_content_hash"),
        table_name,
        "semantic_content_hash IS NULL OR semantic_content_hash ~ '^[0-9a-f]{64}$'",
        schema="careerops_v2",
    )
    op.create_check_constraint(
        op.f(f"ck_{table_name}_normalized_uri"),
        table_name,
        "normalized_uri IS NULL OR normalized_uri LIKE 's3://%'",
        schema="careerops_v2",
    )
    op.create_check_constraint(
        op.f(f"ck_{table_name}_dq_status"),
        table_name,
        "dq_status IS NULL OR dq_status IN ('clean', 'warning', 'blocked')",
        schema="careerops_v2",
    )
    op.create_check_constraint(
        op.f(f"ck_{table_name}_normalized_ref_complete"),
        table_name,
        "(raw_sha256 IS NULL AND normalized_uri IS NULL AND normalized_sha256 IS NULL "
        "AND semantic_content_hash IS NULL AND normalized_schema_version IS NULL "
        "AND dictionary_version IS NULL AND dq_status IS NULL AND processing_ready IS NULL) "
        "OR (raw_sha256 IS NOT NULL AND normalized_uri IS NOT NULL "
        "AND normalized_sha256 IS NOT NULL AND semantic_content_hash IS NOT NULL "
        "AND normalized_schema_version IS NOT NULL "
        "AND length(btrim(normalized_schema_version)) > 0 "
        "AND dictionary_version IS NOT NULL AND length(btrim(dictionary_version)) > 0 "
        "AND dq_status IS NOT NULL AND processing_ready IS NOT NULL)",
        schema="careerops_v2",
    )
    op.create_check_constraint(
        op.f(f"ck_{table_name}_normalized_ref_requires_current"),
        table_name,
        "normalized_uri IS NULL OR materialization_state = 'current'",
        schema="careerops_v2",
    )
    op.create_check_constraint(
        op.f(f"ck_{table_name}_processing_ready_not_blocked"),
        table_name,
        "processing_ready IS DISTINCT FROM TRUE OR dq_status <> 'blocked'",
        schema="careerops_v2",
    )


def upgrade() -> None:
    if not context.is_offline_mode():
        _assert_no_dead_search_tasks()
        _assert_no_application_recovery_rows()

    op.drop_constraint(
        op.f("ck_source_tasks_task_kind"),
        "source_tasks",
        schema="careerops_v2",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_source_tasks_task_kind"),
        "source_tasks",
        "task_kind IN ('search_page', 'vacancy_fetch', 'resume_sync', 'resume_fetch')",
        schema="careerops_v2",
    )

    op.drop_constraint(
        op.f("ck_applications_recovery_provenance"),
        "applications",
        schema="careerops_v2",
        type_="check",
    )
    op.drop_constraint(
        op.f("uq_applications_recovery_source_recovery_record_key"),
        "applications",
        schema="careerops_v2",
        type_="unique",
    )
    for column_name in (
        "imported_at",
        "recovery_record_key",
        "recovery_source",
        "imported_from_legacy",
    ):
        op.drop_column("applications", column_name, schema="careerops_v2")

    for table_name in _NORMALIZED_TABLES:
        _add_normalized_ref_columns(table_name)


def downgrade() -> None:
    for table_name in reversed(_NORMALIZED_TABLES):
        for suffix in reversed(_NORMALIZED_CONSTRAINTS):
            op.drop_constraint(
                op.f(f"ck_{table_name}_{suffix}"),
                table_name,
                schema="careerops_v2",
                type_="check",
            )
        for column_name in reversed(_NORMALIZED_COLUMNS):
            op.drop_column(table_name, column_name, schema="careerops_v2")

    op.add_column(
        "applications",
        sa.Column(
            "imported_from_legacy",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="careerops_v2",
    )
    op.add_column(
        "applications",
        sa.Column("recovery_source", sa.Text()),
        schema="careerops_v2",
    )
    op.add_column(
        "applications",
        sa.Column("recovery_record_key", sa.Text()),
        schema="careerops_v2",
    )
    op.add_column(
        "applications",
        sa.Column("imported_at", sa.TIMESTAMP(timezone=True)),
        schema="careerops_v2",
    )
    op.create_unique_constraint(
        op.f("uq_applications_recovery_source_recovery_record_key"),
        "applications",
        ["recovery_source", "recovery_record_key"],
        schema="careerops_v2",
    )
    op.create_check_constraint(
        op.f("ck_applications_recovery_provenance"),
        "applications",
        "(imported_from_legacy AND recovery_source IS NOT NULL "
        "AND length(btrim(recovery_source)) > 0 AND recovery_record_key IS NOT NULL "
        "AND length(btrim(recovery_record_key)) > 0 AND imported_at IS NOT NULL "
        "AND audit_uri IS NOT NULL AND audit_uri LIKE 's3://%') OR "
        "(NOT imported_from_legacy AND recovery_source IS NULL "
        "AND recovery_record_key IS NULL AND imported_at IS NULL)",
        schema="careerops_v2",
    )

    op.drop_constraint(
        op.f("ck_source_tasks_task_kind"),
        "source_tasks",
        schema="careerops_v2",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_source_tasks_task_kind"),
        "source_tasks",
        "task_kind IN ('search', 'search_page', 'vacancy_fetch', 'resume_sync', 'resume_fetch')",
        schema="careerops_v2",
    )
