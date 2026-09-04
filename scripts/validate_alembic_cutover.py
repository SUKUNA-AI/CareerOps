from __future__ import annotations

import sys

import psycopg
from alembic.util.exc import CommandError
from sqlalchemy.exc import SQLAlchemyError

from careerops_storage.alembic_cutover import (
    CutoverValidationError,
    DisposableDatabaseError,
    format_validation_report,
    load_test_postgres_dsn,
    validate_alembic_cutover,
)


def main() -> int:
    """Validate both Alembic entry paths using only the dedicated test DSN."""

    try:
        dsn = load_test_postgres_dsn()
        report = validate_alembic_cutover(dsn)
    except (
        CommandError,
        CutoverValidationError,
        DisposableDatabaseError,
        FileNotFoundError,
        psycopg.Error,
        SQLAlchemyError,
    ) as exc:
        print(f"CAR-45 real PostgreSQL cutover validation: FAIL: {exc}", file=sys.stderr)
        return 1

    print(format_validation_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
