from __future__ import annotations

import pytest
from support.postgres import (
    UnsafePostgresTestTarget,
    load_postgres_test_target,
    validate_postgres_test_dsn,
)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://user:pass@localhost:5432/careerops_ci_test",
        "postgresql://user:pass@127.0.0.1:5432/careerops-integration-test",
        "host=localhost hostaddr=127.0.0.1 dbname=careerops_testing user=user",
    ],
)
def test_postgres_test_target_accepts_explicit_local_disposable_dsn(dsn: str) -> None:
    target = validate_postgres_test_dsn(dsn)

    assert target.database
    assert target.host in {"localhost", "127.0.0.1", "::1"}


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://user:pass@db.example.com:5432/careerops_ci_test",
        "host=localhost hostaddr=203.0.113.10 dbname=careerops_ci_test user=user",
        "postgresql://user:pass@localhost:5432/postgres",
        "postgresql://user:pass@localhost:5432/careerops",
        "postgresql://user:pass@localhost:5432/careerops_prod_test",
        "host=localhost dbname=careerops_ci_test port=not-a-number",
    ],
)
def test_postgres_test_target_rejects_remote_or_unsafe_dsn(dsn: str) -> None:
    with pytest.raises(UnsafePostgresTestTarget):
        validate_postgres_test_dsn(dsn)


def test_postgres_test_target_has_no_runtime_dsn_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CAREEROPS_TEST_POSTGRES_DSN", raising=False)
    monkeypatch.setenv(
        "CAREEROPS_POSTGRES_DSN",
        "postgresql://user:pass@localhost:5432/careerops_prod",
    )

    assert load_postgres_test_target() is None
