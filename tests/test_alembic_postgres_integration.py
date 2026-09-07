from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from support.postgres import PostgresTestTarget

from alembic import command
from careerops_storage.v2 import SCHEMA, metadata

pytestmark = pytest.mark.integration_postgres

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _head(config: Config) -> str:
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1
    return heads[0]


def _current_revision(target: PostgresTestTarget) -> str | None:
    with psycopg.connect(target.dsn, autocommit=True) as connection:
        relation = connection.execute(
            "SELECT to_regclass('public.alembic_version_v2')::text"
        ).fetchone()
        if relation != ("alembic_version_v2",):
            return None
        row = connection.execute(
            "SELECT version_num FROM public.alembic_version_v2"
        ).fetchone()
    return None if row is None else str(row[0])


def _schema_tables(target: PostgresTestTarget) -> set[str]:
    with psycopg.connect(target.dsn, autocommit=True) as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
            (SCHEMA,),
        ).fetchall()
    return {str(row[0]) for row in rows}


def test_fresh_v2_database_reaches_head_and_matches_metadata_tables(
    v2_postgres_test_target: PostgresTestTarget,
) -> None:
    target = v2_postgres_test_target
    config = _config()
    head = _head(config)

    command.upgrade(config, "head")

    expected_tables = {
        table.name
        for table in metadata.tables.values()
        if table.schema == SCHEMA
    }
    assert _current_revision(target) == head
    assert _schema_tables(target) == expected_tables

    command.upgrade(config, "head")
    assert _current_revision(target) == head
    assert _schema_tables(target) == expected_tables


@pytest.mark.parametrize(
    "legacy_sql",
    [
        "CREATE SCHEMA careerops",
        "CREATE TABLE public.alembic_version (version_num text NOT NULL)",
    ],
)
def test_v2_migrations_refuse_legacy_database(
    v2_postgres_test_target: PostgresTestTarget,
    legacy_sql: str,
) -> None:
    target = v2_postgres_test_target
    with psycopg.connect(target.dsn, autocommit=True) as connection:
        connection.execute(legacy_sql)

    with pytest.raises(RuntimeError, match="refuses a legacy database"):
        command.upgrade(_config(), "head")


def test_alembic_created_v2_database_round_trips_through_base(
    v2_postgres_test_target: PostgresTestTarget,
) -> None:
    target = v2_postgres_test_target
    config = _config()
    head = _head(config)

    command.upgrade(config, "head")
    assert _current_revision(target) == head
    assert _schema_tables(target)

    command.downgrade(config, "base")
    assert _current_revision(target) is None
    assert _schema_tables(target) == set()

    command.upgrade(config, "head")
    assert _current_revision(target) == head
    assert _schema_tables(target)
