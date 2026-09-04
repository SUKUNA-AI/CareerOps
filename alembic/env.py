from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from alembic import context
from careerops_storage.schema import CAREEROPS_SCHEMA
from careerops_storage.schema import metadata as target_metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    configured_url = config.get_main_option("sqlalchemy.url", "").strip()
    # A programmatic caller may have already validated and pinned an explicit
    # database URL.  Keep that URL authoritative so an unrelated runtime DSN
    # cannot redirect migration validation to another PostgreSQL instance.
    raw_url = configured_url or os.getenv("CAREEROPS_POSTGRES_DSN", "").strip()
    if not raw_url:
        raise RuntimeError("Set CAREEROPS_POSTGRES_DSN or provide sqlalchemy.url to run Alembic")

    if raw_url.startswith("postgresql://"):
        raw_url = f"postgresql+psycopg://{raw_url.removeprefix('postgresql://')}"
    elif raw_url.startswith("postgres://"):
        raw_url = f"postgresql+psycopg://{raw_url.removeprefix('postgres://')}"

    try:
        url = make_url(raw_url)
    except ArgumentError as exc:
        raise RuntimeError("Alembic requires a valid PostgreSQL database URL") from exc
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("Alembic is configured only for PostgreSQL")
    return raw_url


def _include_name(
    name: str | None,
    type_: str,
    parent_names: dict[str, str | None],
) -> bool:
    if type_ == "schema":
        return name == CAREEROPS_SCHEMA
    if type_ == "table":
        return parent_names.get("schema_name") == CAREEROPS_SCHEMA
    return True


def run_migrations_offline() -> None:
    """Emit PostgreSQL migration SQL without opening a database connection."""

    # The version table stays in PostgreSQL's default schema because Alembic
    # creates it before the baseline revision creates the careerops schema.
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_name=_include_name,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations through a short-lived SQLAlchemy PostgreSQL engine."""

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # See the offline path above for the intentional version-table location.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_name=_include_name,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
