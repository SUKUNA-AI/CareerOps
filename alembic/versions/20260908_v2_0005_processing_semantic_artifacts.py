"""Добавляет durable индекс переиспользуемых semantic artifacts P2-04"""

import sqlalchemy as sa

from alembic import op

revision = "20260908_v2_0005"
down_revision = "20260908_v2_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processing_semantic_artifacts",
        sa.Column("cache_key", sa.Text(), primary_key=True),
        sa.Column("artifact_kind", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("source_entity_id", sa.Text(), nullable=False),
        sa.Column("account_key", sa.Text()),
        sa.Column("semantic_content_hash", sa.Text(), nullable=False),
        sa.Column("normalized_schema_version", sa.Text(), nullable=False),
        sa.Column("normalization_version", sa.Text(), nullable=False),
        sa.Column("dictionary_version", sa.Text(), nullable=False),
        sa.Column("semantic_version", sa.Text(), nullable=False),
        sa.Column("artifact_schema_version", sa.Text(), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("artifact_sha256", sa.Text(), nullable=False),
        sa.Column("artifact_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "cache_key ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_processing_semantic_artifacts_cache_key"),
        ),
        sa.CheckConstraint(
            "artifact_kind IN ('requirement_set', 'resume_evidence_set')",
            name=op.f("ck_processing_semantic_artifacts_artifact_kind"),
        ),
        sa.CheckConstraint(
            "semantic_content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_processing_semantic_artifacts_semantic_content_hash"),
        ),
        sa.CheckConstraint(
            "artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_processing_semantic_artifacts_artifact_sha256"),
        ),
        sa.CheckConstraint(
            "artifact_uri LIKE 's3://%'",
            name=op.f("ck_processing_semantic_artifacts_artifact_uri"),
        ),
        sa.CheckConstraint(
            "artifact_size_bytes > 0",
            name=op.f("ck_processing_semantic_artifacts_artifact_size_bytes"),
        ),
        sa.CheckConstraint(
            "length(btrim(source_key)) > 0 "
            "AND length(btrim(source_entity_id)) > 0 "
            "AND length(btrim(normalized_schema_version)) > 0 "
            "AND length(btrim(normalization_version)) > 0 "
            "AND length(btrim(dictionary_version)) > 0 "
            "AND length(btrim(semantic_version)) > 0 "
            "AND length(btrim(artifact_schema_version)) > 0",
            name=op.f("ck_processing_semantic_artifacts_non_empty_identity"),
        ),
        sa.CheckConstraint(
            "(artifact_kind = 'requirement_set' AND account_key IS NULL) OR "
            "(artifact_kind = 'resume_evidence_set' AND account_key IS NOT NULL "
            "AND length(btrim(account_key)) > 0)",
            name=op.f("ck_processing_semantic_artifacts_account_scope"),
        ),
        schema="careerops_v2",
    )
    op.create_index(
        "ix_processing_semantic_artifacts_source",
        "processing_semantic_artifacts",
        ["artifact_kind", "source_key", "source_entity_id"],
        schema="careerops_v2",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_processing_semantic_artifacts_source",
        table_name="processing_semantic_artifacts",
        schema="careerops_v2",
    )
    op.drop_table("processing_semantic_artifacts", schema="careerops_v2")
