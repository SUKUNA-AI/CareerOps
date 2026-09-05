"""Canonical v2 migrations; never fall back to the legacy runtime DSN."""

import os
from logging.config import fileConfig

from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from alembic import context
from careerops_storage.v2 import SCHEMA, metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> URL:
    raw_url = os.getenv("CAREEROPS_V2_POSTGRES_DSN", "").strip()
    if not raw_url:
        raise RuntimeError("Online v2 migrations require CAREEROPS_V2_POSTGRES_DSN")
    try:
        url = make_url(raw_url.replace("postgres://", "postgresql://", 1))
    except ArgumentError:
        raise RuntimeError("CAREEROPS_V2_POSTGRES_DSN must be a PostgreSQL URL") from None
    if url.get_backend_name() != "postgresql" or not url.database:
        raise RuntimeError("CAREEROPS_V2_POSTGRES_DSN must select an explicit PostgreSQL database")
    return url.set(drivername="postgresql+psycopg")


def _include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    if type_ == "schema":
        return name == SCHEMA
    if type_ == "table":
        return parent_names.get("schema_name") == SCHEMA
    return True


def run_migrations_offline() -> None:
    context.configure(
        dialect_name="postgresql",
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version_v2",
        version_table_schema="public",
        include_schemas=True,
        include_name=_include_name,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    try:
        with engine.begin() as connection:
            legacy_present = connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'careerops') "
                    "OR to_regclass('public.alembic_version') IS NOT NULL"
                )
            )
            if legacy_present:
                raise RuntimeError("V2 lineage refuses a legacy database; provision a new target")
            context.configure(
                connection=connection,
                target_metadata=metadata,
                version_table="alembic_version_v2",
                version_table_schema="public",
                include_schemas=True,
                include_name=_include_name,
                compare_type=True,
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
