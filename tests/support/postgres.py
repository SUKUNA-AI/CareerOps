"""Shared PostgreSQL test-target safety and lightweight fake helpers."""

from __future__ import annotations

import os
import re
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from types import TracebackType

import psycopg
from psycopg.conninfo import conninfo_to_dict

TEST_POSTGRES_DSN_ENV = "CAREEROPS_TEST_POSTGRES_DSN"
V2_POSTGRES_DSN_ENV = "CAREEROPS_V2_POSTGRES_DSN"

LOCAL_POSTGRES_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
UNSAFE_POSTGRES_DATABASES = frozenset(
    {"careerops", "postgres", "production", "prod", "template0", "template1"}
)
DISPOSABLE_DATABASE_NAME = re.compile(
    r"(?:^|[_-])(?:test|tests|testing|ci|disposable)(?:$|[_-])",
    re.IGNORECASE,
)
PRODUCTION_DATABASE_NAME = re.compile(
    r"(?:^|[_-])(?:prod|production)(?:$|[_-])",
    re.IGNORECASE,
)


class UnsafePostgresTestTarget(ValueError):
    """Configured PostgreSQL target is not explicitly local and disposable."""


@dataclass(frozen=True, slots=True)
class PostgresTestTarget:
    """Validated PostgreSQL target owned by the test suite."""

    dsn: str
    host: str
    database: str


def _split_conninfo_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(item.strip().lower() for item in str(value).split(",") if item.strip())


def validate_postgres_test_dsn(dsn: str) -> PostgresTestTarget:
    """Fail closed unless the target is explicit, local, and clearly disposable."""

    if not dsn.strip():
        raise UnsafePostgresTestTarget(f"{TEST_POSTGRES_DSN_ENV} is empty")

    try:
        parameters = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError as exc:
        raise UnsafePostgresTestTarget(f"{TEST_POSTGRES_DSN_ENV} is invalid") from exc

    if parameters.get("service") or parameters.get("servicefile"):
        raise UnsafePostgresTestTarget(
            "PostgreSQL test DSN must not use libpq service indirection"
        )

    hosts = _split_conninfo_list(parameters.get("host"))
    hostaddrs = _split_conninfo_list(parameters.get("hostaddr"))
    checked_hosts = (*hosts, *hostaddrs)
    if not checked_hosts or any(value not in LOCAL_POSTGRES_HOSTS for value in checked_hosts):
        raise UnsafePostgresTestTarget(
            "PostgreSQL test DSN must explicitly address only localhost, "
            "127.0.0.1, or ::1 via host/hostaddr"
        )

    database = str(parameters.get("dbname") or "").strip()
    if (
        not database
        or database.lower() in UNSAFE_POSTGRES_DATABASES
        or PRODUCTION_DATABASE_NAME.search(database)
        or DISPOSABLE_DATABASE_NAME.search(database) is None
    ):
        raise UnsafePostgresTestTarget(
            "PostgreSQL test database must contain a test/testing/ci/disposable marker "
            "and must not look production-like"
        )

    for port in _split_conninfo_list(parameters.get("port")):
        if not port.isdigit():
            raise UnsafePostgresTestTarget("PostgreSQL test DSN port must be numeric")

    return PostgresTestTarget(
        dsn=dsn,
        host=hosts[0] if hosts else hostaddrs[0],
        database=database,
    )


def load_postgres_test_target() -> PostgresTestTarget | None:
    """Load only the dedicated test DSN; never fall back to runtime v1/v2 DSNs."""

    dsn = os.getenv(TEST_POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        return None
    return validate_postgres_test_dsn(dsn)


def reset_v2_test_database(target: PostgresTestTarget) -> None:
    """Reset only schemas/version tables owned by a validated disposable test database."""

    with psycopg.connect(target.dsn, autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS careerops_v2 CASCADE")
        connection.execute("DROP SCHEMA IF EXISTS careerops CASCADE")
        connection.execute("DROP TABLE IF EXISTS public.alembic_version_v2")
        connection.execute("DROP TABLE IF EXISTS public.alembic_version")


def assert_v2_test_database_empty(target: PostgresTestTarget) -> None:
    """Prove the disposable target has no v1/v2 CareerOPS lineage before a test."""

    with psycopg.connect(target.dsn, autocommit=True) as connection:
        row = connection.execute(
            """
            SELECT
                to_regnamespace('careerops_v2')::text,
                to_regnamespace('careerops')::text,
                to_regclass('public.alembic_version_v2')::text,
                to_regclass('public.alembic_version')::text
            """
        ).fetchone()

    if row != (None, None, None, None):
        raise RuntimeError(f"PostgreSQL test target is not clean: {row!r}")


class TransactionRecorder(AbstractAsyncContextManager[None]):
    """Record transaction boundaries without modelling database state."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def __aenter__(self) -> None:
        self.events.append("begin")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.events.append("rollback" if exc_type else "commit")
        return False
