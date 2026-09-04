from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from careerops_storage.alembic_cutover import (
    PROJECT_ROOT,
    TEST_POSTGRES_DSN_ENV,
    CatalogFingerprint,
    DisposableDatabaseError,
    build_alembic_config,
    load_test_postgres_dsn,
    normalize_catalog_rows,
    validate_disposable_postgres_dsn,
)

LOCAL_TEST_DSN = (
    "postgresql://careerops_car45:local-only@127.0.0.1:55445/"
    "careerops_car45_test"
)


def test_test_dsn_loader_fails_closed_without_runtime_fallback() -> None:
    with pytest.raises(DisposableDatabaseError, match=TEST_POSTGRES_DSN_ENV):
        load_test_postgres_dsn(
            {
                "CAREEROPS_POSTGRES_DSN": (
                    "postgresql://runtime@edge:5432/careerops"
                )
            }
        )


@pytest.mark.parametrize(
    "dsn, expected_host, expected_database",
    [
        (LOCAL_TEST_DSN, "127.0.0.1", "careerops_car45_test"),
        (
            "host=localhost port=5432 dbname=careerops_ci user=ci password=local",
            "localhost",
            "careerops_ci",
        ),
        (
            "host=::1 port=5432 dbname=test_careerops user=ci password=local",
            "::1",
            "test_careerops",
        ),
    ],
)
def test_disposable_dsn_guard_accepts_explicit_local_test_targets(
    dsn: str,
    expected_host: str,
    expected_database: str,
) -> None:
    target = validate_disposable_postgres_dsn(dsn)

    assert target.host == expected_host
    assert target.database == expected_database
    assert make_url(target.sqlalchemy_url).get_backend_name() == "postgresql"
    assert "local-only" not in repr(target)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://ci@10.42.0.1:5432/careerops_car45_test",
        "postgresql://ci@edge:5432/careerops_car45_test",
        "postgresql://ci@core:5432/careerops_car45_test",
        "postgresql://ci@db.example.com:5432/careerops_car45_test",
        "postgresql://ci@localhost:5432/careerops",
        "postgresql://ci@localhost:5432/postgres",
        "postgresql://ci@localhost:5432/careerops_production",
        "postgresql://ci@localhost:5432/careerops_prod_test",
        "host=localhost hostaddr=10.42.0.1 dbname=careerops_car45_test user=ci",
        "dbname=careerops_car45_test user=ci",
    ],
)
def test_disposable_dsn_guard_rejects_remote_or_unsafe_targets(dsn: str) -> None:
    with pytest.raises(DisposableDatabaseError):
        validate_disposable_postgres_dsn(dsn)


def test_catalog_normalization_is_order_independent_and_oid_free() -> None:
    first = normalize_catalog_rows(
        [
            ("vacancies", "title", None),
            ("applications", "id", 1),
        ]
    )
    second = normalize_catalog_rows(
        [
            ("applications", "id", "1"),
            ("vacancies", "title", None),
        ]
    )

    assert first == second
    assert first == (
        ("applications", "id", "1"),
        ("vacancies", "title", None),
    )


def test_catalog_fingerprint_changes_only_with_meaningful_catalog_state() -> None:
    original = CatalogFingerprint(
        schema_exists=True,
        tables=("vacancies",),
        columns=(("vacancies", "1", "id", "bigint"),),
        constraints=(("vacancies", "vacancies_pkey", "p", "PRIMARY KEY (id)"),),
        indexes=(
            (
                "vacancies",
                "vacancies_pkey",
                "CREATE UNIQUE INDEX vacancies_pkey ON careerops.vacancies USING btree (id)",
            ),
        ),
    )
    same = CatalogFingerprint(**vars(original))
    changed = CatalogFingerprint(
        **{
            **vars(original),
            "columns": (("vacancies", "1", "id", "uuid"),),
        }
    )

    assert original.sha256 == same.sha256
    assert original.sha256 != changed.sha256


def test_alembic_config_is_pinned_to_validated_test_database() -> None:
    target = validate_disposable_postgres_dsn(LOCAL_TEST_DSN)
    config = build_alembic_config(target, project_root=PROJECT_ROOT)

    assert config.get_main_option("sqlalchemy.url") == target.sqlalchemy_url
    script_location = config.get_main_option("script_location")
    assert script_location is not None
    assert Path(script_location).resolve() == (PROJECT_ROOT / "alembic").resolve()
