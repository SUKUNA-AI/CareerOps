from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

import careerops_storage.alembic_cutover as cutover
from careerops_storage.alembic_cutover import (
    BASELINE_REVISION,
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


def _catalog(table_name: str) -> CatalogFingerprint:
    return CatalogFingerprint(
        schema_exists=True,
        tables=(table_name,),
        columns=(),
        constraints=(),
        indexes=(),
    )


def _schema_summary() -> cutover.LiveSchemaSummary:
    return cutover.LiveSchemaSummary(
        tables=("source_profiles",),
        column_count=1,
        constraint_count=1,
        index_count=1,
        identity_columns=(),
        partial_indexes=(),
    )


def test_live_schema_accepts_future_v2_table_declared_by_metadata() -> None:
    future_metadata = sa.MetaData()
    sa.Table(
        "search_query_states",
        future_metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
        schema=cutover.CAREEROPS_SCHEMA,
    )
    fingerprint = CatalogFingerprint(
        schema_exists=True,
        tables=("search_query_states",),
        columns=(
            (
                "search_query_states",
                "1",
                "id",
                "bigint",
                "pg_catalog",
                "int8",
                "NO",
                None,
                "NO",
                None,
            ),
        ),
        constraints=(
            (
                "search_query_states",
                "search_query_states_pkey",
                "p",
                "PRIMARY KEY (id)",
            ),
        ),
        indexes=(),
    )

    summary = cutover.validate_live_schema(
        fingerprint,
        target_metadata=future_metadata,
    )

    assert summary.tables == ("search_query_states",)
    assert summary.column_count == 1


def test_manual_fresh_path_uses_dynamic_graph_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = validate_disposable_postgres_dsn(LOCAL_TEST_DSN)
    future_head = "20261001_future_head"
    catalog = _catalog("fresh_head")
    schema = _schema_summary()
    upgrades: list[str] = []
    metadata_comparisons: list[cutover.DisposablePostgresTarget] = []

    monkeypatch.setattr(cutover, "get_single_alembic_head", lambda config: future_head)
    monkeypatch.setattr(
        cutover.command,
        "upgrade",
        lambda config, revision: upgrades.append(revision),
    )
    monkeypatch.setattr(
        cutover,
        "read_alembic_revision",
        lambda actual_target: future_head,
    )
    monkeypatch.setattr(
        cutover,
        "capture_catalog_fingerprint",
        lambda actual_target: catalog,
    )
    monkeypatch.setattr(
        cutover,
        "validate_live_schema",
        lambda actual_catalog: schema,
    )
    monkeypatch.setattr(
        cutover,
        "compare_live_schema_to_metadata",
        lambda actual_target: metadata_comparisons.append(actual_target) or (),
    )

    report = cutover._fresh_path(target, PROJECT_ROOT)

    assert report.head_revision == future_head
    assert report.second_upgrade_was_noop is True
    assert upgrades == ["head", "head"]
    assert metadata_comparisons == [target]


def test_manual_legacy_path_stamps_baseline_then_upgrades_to_dynamic_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = validate_disposable_postgres_dsn(LOCAL_TEST_DSN)
    future_revisions = ("20261001_first", "20261002_future_head")
    future_head = future_revisions[-1]
    legacy_catalog = _catalog("legacy_baseline")
    head_catalog = _catalog("future_head")
    schema = _schema_summary()
    revision_states = iter((None, BASELINE_REVISION, future_head))
    catalog_states = iter((legacy_catalog, legacy_catalog, head_catalog))
    stamps: list[str] = []
    upgrades: list[str] = []
    validated_catalogs: list[CatalogFingerprint] = []
    metadata_comparisons: list[cutover.DisposablePostgresTarget] = []

    monkeypatch.setattr(cutover, "get_single_alembic_head", lambda config: future_head)
    monkeypatch.setattr(
        cutover,
        "get_revisions_after",
        lambda config, revision: future_revisions,
    )
    monkeypatch.setattr(
        cutover,
        "apply_legacy_migrations",
        lambda actual_target, *, project_root: cutover.LEGACY_MIGRATIONS,
    )
    monkeypatch.setattr(
        cutover,
        "read_alembic_revision",
        lambda actual_target: next(revision_states),
    )
    monkeypatch.setattr(
        cutover,
        "capture_catalog_fingerprint",
        lambda actual_target: next(catalog_states),
    )
    monkeypatch.setattr(
        cutover.command,
        "stamp",
        lambda config, revision: stamps.append(revision),
    )
    monkeypatch.setattr(
        cutover.command,
        "upgrade",
        lambda config, revision: upgrades.append(revision),
    )

    def validate_head_catalog(actual_catalog: CatalogFingerprint) -> cutover.LiveSchemaSummary:
        validated_catalogs.append(actual_catalog)
        return schema

    monkeypatch.setattr(cutover, "validate_live_schema", validate_head_catalog)
    monkeypatch.setattr(
        cutover,
        "compare_live_schema_to_metadata",
        lambda actual_target: metadata_comparisons.append(actual_target) or (),
    )

    report = cutover._legacy_path(target, PROJECT_ROOT)

    assert report.baseline_revision == BASELINE_REVISION
    assert report.head_revision == future_head
    assert report.revisions_after_baseline == future_revisions
    assert report.stamp_catalog_unchanged is True
    assert report.post_stamp_upgrade_was_noop is False
    assert stamps == [BASELINE_REVISION]
    assert upgrades == ["head"]
    assert validated_catalogs == [head_catalog]
    assert metadata_comparisons == [target]

    validation_report = cutover.CutoverValidationReport(
        postgresql_version="PostgreSQL test",
        database="careerops_test",
        host="127.0.0.1",
        baseline_revision=BASELINE_REVISION,
        head_revision=future_head,
        fresh=cutover.FreshPathReport(
            head_revision=future_head,
            schema=schema,
            catalog_sha256=head_catalog.sha256,
            second_upgrade_was_noop=True,
        ),
        legacy=report,
    )
    formatted = cutover.format_validation_report(validation_report)
    assert "revisions after baseline: 20261001_first, 20261002_future_head" in formatted
    assert "applied 2 revision(s); reached graph head 20261002_future_head" in formatted
    assert "upgrade head after stamp: PASS (no-op" not in formatted


def test_manual_report_marks_post_stamp_upgrade_noop_without_descendants() -> None:
    schema = _schema_summary()
    catalog = _catalog("baseline_head")
    legacy = cutover.LegacyPathReport(
        baseline_revision=BASELINE_REVISION,
        head_revision=BASELINE_REVISION,
        revisions_after_baseline=(),
        schema=schema,
        migrations=cutover.LEGACY_MIGRATIONS,
        pre_stamp_catalog_sha256=catalog.sha256,
        post_upgrade_catalog_sha256=catalog.sha256,
        stamp_catalog_unchanged=True,
    )
    report = cutover.CutoverValidationReport(
        postgresql_version="PostgreSQL test",
        database="careerops_test",
        host="localhost",
        baseline_revision=BASELINE_REVISION,
        head_revision=BASELINE_REVISION,
        fresh=cutover.FreshPathReport(
            head_revision=BASELINE_REVISION,
            schema=schema,
            catalog_sha256=catalog.sha256,
            second_upgrade_was_noop=True,
        ),
        legacy=legacy,
    )

    assert legacy.post_stamp_upgrade_was_noop is True
    formatted = cutover.format_validation_report(report)
    assert "revisions after baseline: none" in formatted
    assert "no-op; baseline is graph head 20260904_0005" in formatted
