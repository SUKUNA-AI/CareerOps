"""CAR-48 add search query states

Revision ID: 20260905_0048
Revises: 20260904_0005
Create Date: 2026-09-05 16:05:34.761680

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '20260905_0048'
down_revision: str | Sequence[str] | None = '20260904_0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_query_states",

        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),

        sa.Column(
            "source_profile_id",
            sa.BigInteger(),
            nullable=False,
        ),

        sa.Column(
            "account_key",
            sa.Text(),
            nullable=False,
        ),

        sa.Column(
            "query_key",
            sa.Text(),
            nullable=False,
        ),

        sa.Column(
            "query_set_key",
            sa.Text(),
            nullable=False,
        ),

        sa.Column(
            "query_signature",
            sa.Text(),
            nullable=False,
        ),

        sa.Column(
            "request_params",
            postgresql.JSONB(),
            nullable=False,
        ),

        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),

        sa.Column(
            "retired_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["source_profile_id"],
            ["careerops.source_profiles.id"],
        ),

        sa.PrimaryKeyConstraint(
            "id",
        ),

        sa.UniqueConstraint(
            "source_profile_id",
            "query_key",
            "query_signature",
            name="search_query_states_profile_query_signature_uk",
        ),

        sa.CheckConstraint(
            "query_signature ~ '^[0-9a-f]{64}$'",
            name="search_query_states_signature_ck",
        ),

        sa.CheckConstraint(
            "jsonb_typeof(request_params) = 'object'",
            name="search_query_states_request_params_ck",
        ),

        sa.CheckConstraint(
            """
            btrim(account_key) <> ''
            AND btrim(query_key) <> ''
            AND btrim(query_set_key) <> ''
            """,
            name="search_query_states_keys_ck",
        ),

        sa.CheckConstraint(
            """
            (
                is_active
                AND retired_at IS NULL
            )
            OR
            (
                NOT is_active
                AND retired_at IS NOT NULL
            )
            """,
            name="search_query_states_lifecycle_ck",
        ),

        schema="careerops",
    )

    op.create_index(
        "search_query_states_active_profile_query_uk",
        "search_query_states",
        [
            "source_profile_id",
            "query_key",
        ],
        unique=True,
        schema="careerops",
        postgresql_where=sa.text("is_active IS true"),
    )


def downgrade() -> None:
    op.drop_index(
        "search_query_states_active_profile_query_uk",
        table_name="search_query_states",
        schema="careerops",
    )

    op.drop_table(
        "search_query_states",
        schema="careerops",
    )
