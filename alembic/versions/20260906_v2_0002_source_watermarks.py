"""Хранит HH publication watermarks для lossless adaptive paging"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260906_v2_0002"
down_revision = "20260906_v2_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_watermarks",
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("profile_id", sa.BigInteger(), nullable=False),
        sa.Column("query_key", sa.Text(), nullable=False),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("generation_id", sa.UUID(), nullable=False),
        sa.Column("observed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(query_key)) > 0",
            name=op.f("ck_source_watermarks_query_key"),
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "account_id", "source_id"],
            [
                "careerops_v2.profiles.id",
                "careerops_v2.profiles.account_id",
                "careerops_v2.profiles.source_id",
            ],
            name=op.f("fk_source_watermarks_profile_id_profiles"),
        ),
        sa.PrimaryKeyConstraint(
            "account_id",
            "profile_id",
            "query_key",
            name=op.f("pk_source_watermarks"),
        ),
        schema="careerops_v2",
    )
    op.create_index(
        "ix_source_watermarks_profile",
        "source_watermarks",
        ["account_id", "profile_id"],
        unique=False,
        schema="careerops_v2",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_source_watermarks_profile",
        table_name="source_watermarks",
        schema="careerops_v2",
    )
    op.drop_table("source_watermarks", schema="careerops_v2")
