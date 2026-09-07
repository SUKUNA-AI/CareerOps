from __future__ import annotations

import psycopg
import pytest
from support.postgres import PostgresTestTarget

pytestmark = pytest.mark.integration_postgres

EXPECTED_POSTGRES_VERSION_NUM = "180006"


def test_postgres_ci_runner_uses_pinned_18_6_service(
    postgres_test_target: PostgresTestTarget,
) -> None:
    with psycopg.connect(postgres_test_target.dsn) as connection:
        version_num = connection.execute("SHOW server_version_num").fetchone()
        database = connection.execute("SELECT current_database()").fetchone()

    assert version_num == (EXPECTED_POSTGRES_VERSION_NUM,)
    assert database == (postgres_test_target.database,)


def test_postgres_ci_runner_preserves_transaction_rollback(
    postgres_test_target: PostgresTestTarget,
) -> None:
    with psycopg.connect(postgres_test_target.dsn, autocommit=True) as connection:
        connection.execute("CREATE TEMP TABLE careerops_ci_probe (value integer NOT NULL)")

        with pytest.raises(RuntimeError, match="rollback probe"):
            with connection.transaction():
                connection.execute("INSERT INTO careerops_ci_probe (value) VALUES (1)")
                raise RuntimeError("rollback probe")

        row = connection.execute("SELECT count(*) FROM careerops_ci_probe").fetchone()

    assert row == (0,)
