"""Shared helpers for fake-backed and real PostgreSQL tests."""

from __future__ import annotations

import os
import re
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from types import TracebackType

import psycopg
from psycopg.conninfo import conninfo_to_dict

TEST_POSTGRES_DSN_ENV = "CAREEROPS_TEST_POSTGRES_DSN"
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
    """The configured PostgreSQL target is not explicitly disposable and local."""


@dataclass(frozen=True, slots=True)
class PostgresTestTarget:
    """A PostgreSQL DSN proven suitable for destructive/integration test work."""

    dsn: str
    host: str
    database: str


def validate_postgres_test_dsn(dsn: str) -> PostgresTestTarget:
    """Fail closed unless the DSN is explicitly local and clearly disposable."""

    if not dsn.strip():
        raise UnsafePostgresTestTarget("CAREEROPS_TEST_POSTGRES_DSN is empty")

    try:
        parameters = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError as exc:
        raise UnsafePostgresTestTarget("CAREEROPS_TEST_POSTGRES_DSN is invalid") from exc

    host = str(parameters.get("host") or "").lower()
    hostaddr = str(parameters.get("hostaddr") or "").lower()
    checked_hosts = tuple(value for value in (host, hostaddr) if value)
    if not checked_hosts or any(value not in LOCAL_POSTGRES_HOSTS for value in checked_hosts):
        raise UnsafePostgresTestTarget(
            "CAREEROPS_TEST_POSTGRES_DSN must explicitly address localhost, "
            "127.0.0.1, or ::1 via both host and hostaddr when present"
        )

    database = str(parameters.get("dbname") or "")
    if (
        not database
        or database.lower() in UNSAFE_POSTGRES_DATABASES
        or PRODUCTION_DATABASE_NAME.search(database)
        or DISPOSABLE_DATABASE_NAME.search(database) is None
    ):
        raise UnsafePostgresTestTarget(
            "CAREEROPS_TEST_POSTGRES_DSN database name must contain a separate "
            "test, testing, ci, or disposable marker and must not look production-like"
        )

    port_value = parameters.get("port")
    if port_value is not None:
        try:
            int(port_value)
        except ValueError as exc:
            raise UnsafePostgresTestTarget("PostgreSQL test DSN port must be numeric") from exc

    return PostgresTestTarget(dsn=dsn, host=host or hostaddr, database=database)


def load_postgres_test_target() -> PostgresTestTarget | None:
    """Load the dedicated guarded test target without any runtime DSN fallback."""

    dsn = os.getenv(TEST_POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        return None
    return validate_postgres_test_dsn(dsn)


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
