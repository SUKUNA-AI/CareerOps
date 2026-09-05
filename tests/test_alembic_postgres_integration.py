from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest
from alembic.config import Config

from alembic import command
from careerops_storage.alembic_cutover import (
    BASELINE_REVISION,
    LEGACY_MIGRATIONS,
    PROJECT_ROOT,
    TEST_POSTGRES_DSN_ENV,
    DisposablePostgresTarget,
    apply_legacy_migrations,
    assert_disposable_state_is_empty,
    build_alembic_config,
    capture_catalog_fingerprint,
    compare_live_schema_to_metadata,
    get_revisions_after,
    get_single_alembic_head,
    read_alembic_revision,
    reset_disposable_state,
    validate_disposable_postgres_dsn,
)

pytestmark = pytest.mark.integration_postgres

ORCHESTRATION_RELATIONS = (
    "careerops.source_profiles",
    "careerops.resumes",
    "careerops.vacancies",
    "careerops.application_claims",
    "careerops.observe_query_cursors",
    "careerops.observation_runs",
    "careerops.vacancy_observations",
    "careerops.evaluation_work_items",
)


@pytest.fixture
def disposable_postgres_target(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[DisposablePostgresTarget]:
    dsn = os.getenv(TEST_POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{TEST_POSTGRES_DSN_ENV} is not configured")

    target = validate_disposable_postgres_dsn(dsn)
    # Every Alembic command receives the guarded URL through Config. A poison
    # runtime DSN makes this safety property fail loudly if precedence regresses.
    monkeypatch.setenv("CAREEROPS_POSTGRES_DSN", "sqlite:///must-not-win")
    reset_disposable_state(target)
    assert_disposable_state_is_empty(target)
    try:
        yield target
    finally:
        reset_disposable_state(target)


def _upgrade_to_graph_head(
    target: DisposablePostgresTarget,
) -> tuple[Config, str]:
    config = build_alembic_config(target)
    head = get_single_alembic_head(config)
    command.upgrade(config, "head")
    assert read_alembic_revision(target) == head
    return config, head


def test_fresh_database_reaches_head_matches_metadata_and_is_idempotent(
    disposable_postgres_target: DisposablePostgresTarget,
) -> None:
    target = disposable_postgres_target
    config, head = _upgrade_to_graph_head(target)

    first_catalog = capture_catalog_fingerprint(target)
    assert first_catalog.schema_exists is True
    assert compare_live_schema_to_metadata(target) == ()

    command.upgrade(config, "head")
    assert read_alembic_revision(target) == head
    assert capture_catalog_fingerprint(target) == first_catalog


def test_legacy_stamp_preserves_schema_then_applies_only_descendants(
    disposable_postgres_target: DisposablePostgresTarget,
) -> None:
    target = disposable_postgres_target
    config = build_alembic_config(target)
    head = get_single_alembic_head(config)
    revisions_after_baseline = get_revisions_after(config, BASELINE_REVISION)
    assert BASELINE_REVISION not in revisions_after_baseline
    if revisions_after_baseline:
        assert revisions_after_baseline[-1] == head
    else:
        assert head == BASELINE_REVISION

    assert apply_legacy_migrations(target, project_root=PROJECT_ROOT) == LEGACY_MIGRATIONS
    with psycopg.connect(target.dsn, autocommit=True) as connection:
        for relation in ORCHESTRATION_RELATIONS:
            row = connection.execute(
                "SELECT to_regclass(%s)::text",
                (relation,),
            ).fetchone()
            assert row == (relation,)
        identity = connection.execute(
            "SELECT pg_get_constraintdef(oid) "
            "FROM pg_constraint "
            "WHERE conrelid = 'careerops.application_claims'::regclass "
            "AND conname = 'application_claims_identity_uk'"
        ).fetchone()
        assert identity == ("UNIQUE (resume_id, vacancy_id)",)

    before_stamp = capture_catalog_fingerprint(target)
    assert read_alembic_revision(target) is None

    command.stamp(config, BASELINE_REVISION)
    assert read_alembic_revision(target) == BASELINE_REVISION
    after_stamp = capture_catalog_fingerprint(target)
    assert after_stamp == before_stamp

    command.upgrade(config, "head")
    assert read_alembic_revision(target) == head
    assert compare_live_schema_to_metadata(target) == ()
    if not revisions_after_baseline:
        assert capture_catalog_fingerprint(target) == after_stamp


def test_metadata_drift_detector_reports_controlled_extra_column(
    disposable_postgres_target: DisposablePostgresTarget,
) -> None:
    target = disposable_postgres_target
    _upgrade_to_graph_head(target)
    assert compare_live_schema_to_metadata(target) == ()

    with psycopg.connect(target.dsn, autocommit=True) as connection:
        connection.execute(
            "ALTER TABLE careerops.source_profiles "
            "ADD COLUMN car46_unexpected_column text"
        )

    differences = compare_live_schema_to_metadata(target)
    extra_column_diffs = [
        difference
        for difference in differences
        if isinstance(difference, tuple)
        and len(difference) >= 4
        and difference[0] == "remove_column"
        and difference[1] == "careerops"
        and difference[2] == "source_profiles"
        and getattr(difference[3], "name", None) == "car46_unexpected_column"
    ]
    assert extra_column_diffs, repr(differences)


def test_alembic_created_database_can_round_trip_through_base(
    disposable_postgres_target: DisposablePostgresTarget,
) -> None:
    """Downgrade only a schema created by Alembic, never a stamped legacy DB."""

    target = disposable_postgres_target
    config, head = _upgrade_to_graph_head(target)
    assert compare_live_schema_to_metadata(target) == ()

    command.downgrade(config, "base")
    downgraded = capture_catalog_fingerprint(target)
    assert downgraded.schema_exists is False
    assert downgraded.tables == ()
    assert read_alembic_revision(target) is None

    command.upgrade(config, "head")
    assert read_alembic_revision(target) == head
    assert compare_live_schema_to_metadata(target) == ()
