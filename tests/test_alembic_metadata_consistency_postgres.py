from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from support.postgres import PostgresTestTarget

from alembic import command
from careerops_storage.v2 import SCHEMA, metadata

pytestmark = pytest.mark.integration_postgres
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def test_alembic_head_has_no_schema_drift_from_canonical_metadata(
    v2_postgres_test_target: PostgresTestTarget,
) -> None:
    target = v2_postgres_test_target
    command.upgrade(_config(), "head")

    url = make_url(target.dsn).set(drivername="postgresql+psycopg")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    "include_schemas": True,
                    "include_name": lambda name, type_, parent_names: (
                        type_ != "schema" or name in {None, SCHEMA}
                    ),
                },
            )
            differences = compare_metadata(context, metadata)
    finally:
        engine.dispose()

    assert differences == [], "Alembic head drifted from SQLAlchemy metadata:\n" + "\n".join(
        repr(item) for item in differences
    )
