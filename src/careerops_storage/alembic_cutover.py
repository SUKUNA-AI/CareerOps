"""Focused real-PostgreSQL proof for the legacy-to-Alembic cutover."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import psycopg
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import (
    CheckConstraint,
    MetaData,
    UniqueConstraint,
    create_engine,
    inspect,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, UUID
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from alembic import command
from careerops_storage.schema import CAREEROPS_SCHEMA, metadata

TEST_POSTGRES_DSN_ENV: Final = "CAREEROPS_TEST_POSTGRES_DSN"
BASELINE_REVISION: Final = "20260904_0005"
PROJECT_ROOT: Final = Path(__file__).resolve().parents[2]
LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})
UNSAFE_DATABASES: Final = frozenset(
    {"careerops", "postgres", "production", "prod", "template0", "template1"}
)
DISPOSABLE_NAME = re.compile(
    r"(?:^|[_-])(?:test|tests|testing|ci|disposable)(?:$|[_-])",
    re.IGNORECASE,
)
PRODUCTION_NAME = re.compile(
    r"(?:^|[_-])(?:prod|production)(?:$|[_-])",
    re.IGNORECASE,
)

TYPE_PROBES: Final = {
    ("batch_runs", "id"): "uuid",
    ("vacancy_decisions", "metadata"): "jsonb",
    ("observation_runs", "query_keys"): "ARRAY",
    ("applications", "requested_at"): "timestamp with time zone",
}
REPAIRED_NULLABLE: Final = frozenset(
    {
        ("vacancies", "title"),
        ("batch_runs", "discovered"),
        ("batch_runs", "prefiltered"),
        ("batch_runs", "full_fetched"),
        ("batch_runs", "accepted"),
        ("batch_runs", "submitted"),
        ("batch_runs", "confirmed"),
        ("batch_runs", "failed"),
        ("batch_runs", "stopped_on_captcha"),
    }
)
REPAIRED_AUDIT_NOT_NULL = frozenset(
    (table_name, column_name)
    for table_name in ("resumes", "vacancies", "batch_runs", "applications")
    for column_name in ("created_at", "updated_at")
)
LEGACY_MIGRATIONS: Final = (
    "0001_create_oltp_core.sql",
    "0002_add_oltp_indexes.sql",
    "0003_add_hh_application_claims.sql",
    "0004_add_hh_orchestration_state.sql",
    "0005_repair_legacy_oltp_schema.sql",
)

CatalogRow = tuple[str | None, ...]


class DisposableDatabaseError(ValueError):
    """The destructive target is not explicitly disposable."""


class CutoverValidationError(RuntimeError):
    """A live cutover invariant failed."""


@dataclass(frozen=True, repr=False)
class DisposablePostgresTarget:
    dsn: str
    host: str
    database: str
    sqlalchemy_url: str

    def __repr__(self) -> str:
        return f"DisposablePostgresTarget(host={self.host!r}, database={self.database!r})"


@dataclass(frozen=True)
class CatalogFingerprint:
    """Stable CareerOPS catalog state with no OIDs or other internal IDs."""

    schema_exists: bool
    tables: tuple[str, ...]
    columns: tuple[CatalogRow, ...]
    constraints: tuple[CatalogRow, ...]
    indexes: tuple[CatalogRow, ...]

    @property
    def sha256(self) -> str:
        encoded = json.dumps(
            vars(self),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PrimaryKeyDrift:
    """A PK difference Alembic autogenerate does not report itself."""

    schema: str
    table: str
    metadata_columns: tuple[str, ...]
    database_columns: tuple[str, ...]


@dataclass(frozen=True)
class LiveSchemaSummary:
    tables: tuple[str, ...]
    column_count: int
    constraint_count: int
    index_count: int
    identity_columns: tuple[tuple[str, str], ...]
    partial_indexes: tuple[str, ...]


@dataclass(frozen=True)
class FreshPathReport:
    head_revision: str
    schema: LiveSchemaSummary
    catalog_sha256: str
    second_upgrade_was_noop: bool


@dataclass(frozen=True)
class LegacyPathReport:
    baseline_revision: str
    head_revision: str
    revisions_after_baseline: tuple[str, ...]
    schema: LiveSchemaSummary
    migrations: tuple[str, ...]
    pre_stamp_catalog_sha256: str
    post_upgrade_catalog_sha256: str
    stamp_catalog_unchanged: bool

    @property
    def post_stamp_upgrade_was_noop(self) -> bool:
        return not self.revisions_after_baseline


@dataclass(frozen=True)
class CutoverValidationReport:
    postgresql_version: str
    database: str
    host: str
    baseline_revision: str
    head_revision: str
    fresh: FreshPathReport
    legacy: LegacyPathReport


def load_test_postgres_dsn(environ: Mapping[str, str] | None = None) -> str:
    """Read only the dedicated test DSN; never fall back to runtime config."""

    source = os.environ if environ is None else environ
    dsn = source.get(TEST_POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        raise DisposableDatabaseError(
            f"{TEST_POSTGRES_DSN_ENV} is required; no runtime DSN fallback is allowed"
        )
    return dsn


def validate_disposable_postgres_dsn(dsn: str) -> DisposablePostgresTarget:
    """Require a local host and an unmistakably disposable database name."""

    if not dsn.strip():
        raise DisposableDatabaseError("the disposable PostgreSQL DSN is empty")
    try:
        parameters = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError as exc:
        raise DisposableDatabaseError("the disposable PostgreSQL DSN is invalid") from exc

    host = str(parameters.get("host") or "").lower()
    hostaddr = str(parameters.get("hostaddr") or "").lower()
    checked_hosts = tuple(value for value in (host, hostaddr) if value)
    if not checked_hosts or any(value not in LOCAL_HOSTS for value in checked_hosts):
        raise DisposableDatabaseError(
            "CAREEROPS_TEST_POSTGRES_DSN must explicitly address localhost, "
            "127.0.0.1, or ::1"
        )

    database = str(parameters.get("dbname") or "")
    if database.lower() in UNSAFE_DATABASES or PRODUCTION_NAME.search(database):
        raise DisposableDatabaseError(
            f"database {database!r} is not an allowed destructive test target"
        )
    if not database or DISPOSABLE_NAME.search(database) is None:
        raise DisposableDatabaseError(
            "the database name must contain a separate test, testing, ci, or "
            "disposable marker"
        )

    try:
        port_value = parameters.get("port")
        port = None if port_value is None else int(port_value)
    except ValueError as exc:
        raise DisposableDatabaseError("the PostgreSQL DSN must use one numeric port") from exc

    core_keys = {"user", "password", "host", "port", "dbname"}
    query: dict[str, str] = {
        key: str(value)
        for key, value in parameters.items()
        if key not in core_keys and value
    }
    username = parameters.get("user")
    password = parameters.get("password")
    url = URL.create(
        "postgresql+psycopg",
        username=None if username is None else str(username),
        password=None if password is None else str(password),
        host=host or hostaddr,
        port=port,
        database=database,
        query=query,
    )
    return DisposablePostgresTarget(
        dsn=dsn,
        host=host or hostaddr,
        database=database,
        sqlalchemy_url=url.render_as_string(hide_password=False),
    )


def normalize_catalog_rows(rows: Iterable[Sequence[object]]) -> tuple[CatalogRow, ...]:
    normalized = [
        tuple(None if value is None else str(value) for value in row)
        for row in rows
    ]
    return tuple(
        sorted(
            normalized,
            key=lambda row: tuple((value is not None, value or "") for value in row),
        )
    )


def build_alembic_config(
    target: DisposablePostgresTarget,
    *,
    project_root: Path | None = None,
) -> Config:
    """Pin Alembic to the already guarded test URL."""

    root = PROJECT_ROOT if project_root is None else project_root.resolve()
    config_path = root / "alembic.ini"
    if not config_path.is_file() or not (root / "alembic").is_dir():
        raise FileNotFoundError("the Alembic configuration is incomplete")
    config = Config(str(config_path))
    config.set_main_option("sqlalchemy.url", target.sqlalchemy_url.replace("%", "%%"))
    return config


def get_single_alembic_head(config: Config) -> str:
    """Return the graph-derived head, rejecting a branched migration graph."""

    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise CutoverValidationError(f"expected exactly one Alembic head, found {heads!r}")
    return heads[0]


def get_revisions_after(
    config: Config,
    revision: str,
) -> tuple[str, ...]:
    """Return descendants after ``revision`` in upgrade execution order."""

    script = ScriptDirectory.from_config(config)
    head = get_single_alembic_head(config)
    descending = tuple(script.iterate_revisions(head, revision))
    return tuple(item.revision for item in reversed(descending))


def _rows(connection: psycopg.Connection[Any], query: str) -> list[tuple[object, ...]]:
    return [tuple(row) for row in connection.execute(query).fetchall()]


def _scalar(connection: psycopg.Connection[Any], query: str) -> object:
    row = connection.execute(query).fetchone()
    if row is None:
        raise CutoverValidationError("a PostgreSQL catalog query returned no row")
    value: object = row[0]
    return value


def reset_disposable_state(target: DisposablePostgresTarget) -> None:
    """Drop only migration-test-managed objects in the guarded test database."""

    with psycopg.connect(target.dsn, autocommit=True) as connection:
        if str(_scalar(connection, "SELECT current_database()")) != target.database:
            raise DisposableDatabaseError("connected database differs from the guarded DSN")
        connection.execute(f"DROP SCHEMA IF EXISTS {CAREEROPS_SCHEMA} CASCADE")
        connection.execute("DROP TABLE IF EXISTS public.alembic_version")


def assert_disposable_state_is_empty(target: DisposablePostgresTarget) -> None:
    """Prove a scoped reset left no user schema or relation behind."""

    with psycopg.connect(target.dsn) as connection:
        schemas = _rows(
            connection,
            """SELECT nspname FROM pg_namespace
               WHERE nspname NOT IN ('information_schema', 'pg_catalog', 'public')
                 AND nspname NOT LIKE 'pg_%' ORDER BY nspname""",
        )
        relations = _rows(
            connection,
            """SELECT n.nspname, c.relname, c.relkind
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
                 AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
               ORDER BY n.nspname, c.relname, c.relkind""",
        )
    if schemas or relations:
        raise CutoverValidationError(
            "the disposable database is not empty after the scoped reset; "
            f"schemas={schemas!r}, relations={relations!r}"
        )


def capture_catalog_fingerprint(target: DisposablePostgresTarget) -> CatalogFingerprint:
    """Capture tables, columns, constraints, and indexes without internal IDs."""

    with psycopg.connect(target.dsn) as connection:
        schema_exists = bool(
            _scalar(
                connection,
                "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'careerops')",
            )
        )
        tables = normalize_catalog_rows(
            _rows(
                connection,
                """SELECT table_name FROM information_schema.tables
                   WHERE table_schema = 'careerops' AND table_type = 'BASE TABLE'
                   ORDER BY table_name""",
            )
        )
        columns = normalize_catalog_rows(
            _rows(
                connection,
                """SELECT table_name, ordinal_position, column_name, data_type,
                          udt_schema, udt_name, is_nullable, column_default,
                          is_identity, identity_generation
                   FROM information_schema.columns WHERE table_schema = 'careerops'
                   ORDER BY table_name, ordinal_position""",
            )
        )
        constraints = normalize_catalog_rows(
            _rows(
                connection,
                """SELECT r.relname, c.conname, c.contype, pg_get_constraintdef(c.oid, true)
                   FROM pg_constraint c JOIN pg_class r ON r.oid = c.conrelid
                   JOIN pg_namespace n ON n.oid = r.relnamespace
                   WHERE n.nspname = 'careerops' ORDER BY r.relname, c.conname""",
            )
        )
        indexes = normalize_catalog_rows(
            _rows(
                connection,
                """SELECT tablename, indexname, indexdef FROM pg_indexes
                   WHERE schemaname = 'careerops' ORDER BY tablename, indexname""",
            )
        )
    return CatalogFingerprint(
        schema_exists=schema_exists,
        tables=tuple(str(row[0]) for row in tables),
        columns=columns,
        constraints=constraints,
        indexes=indexes,
    )


def _include_careerops_name(
    name: str | None,
    type_: str,
    parent_names: dict[str, str | None],
) -> bool:
    if type_ == "schema":
        return name == CAREEROPS_SCHEMA
    if type_ == "table":
        return parent_names.get("schema_name") == CAREEROPS_SCHEMA
    return True


def compare_live_schema_to_metadata(
    target: DisposablePostgresTarget,
    *,
    target_metadata: MetaData = metadata,
) -> tuple[Any, ...]:
    """Return unfiltered Alembic diffs plus semantic PK differences.

    Reflection is limited to ``careerops``, which also excludes Alembic's
    intentionally public version table. No diff categories are suppressed:
    a clean PostgreSQL schema must compare as an actual empty result.
    """

    engine = create_engine(target.sqlalchemy_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection=connection,
                opts={
                    "include_schemas": True,
                    "include_name": _include_careerops_name,
                    "compare_type": True,
                    "compare_server_default": True,
                },
            )
            differences: list[Any] = list(compare_metadata(context, target_metadata))

            # Alembic autogenerate does not compare primary keys. Derive the
            # expected columns from MetaData instead of maintaining a whitelist.
            inspector = inspect(connection)
            for table in target_metadata.sorted_tables:
                if table.schema != CAREEROPS_SCHEMA:
                    continue
                if not inspector.has_table(table.name, schema=CAREEROPS_SCHEMA):
                    # Alembic already reports the missing table itself.
                    continue
                reflected = inspector.get_pk_constraint(
                    table.name,
                    schema=CAREEROPS_SCHEMA,
                )
                database_columns = tuple(
                    str(column)
                    for column in reflected.get("constrained_columns", ())
                )
                metadata_columns = tuple(table.primary_key.columns.keys())
                if database_columns != metadata_columns:
                    differences.append(
                        PrimaryKeyDrift(
                            schema=CAREEROPS_SCHEMA,
                            table=table.name,
                            metadata_columns=metadata_columns,
                            database_columns=database_columns,
                        )
                    )
            return tuple(differences)
    finally:
        engine.dispose()


def _assert_live_schema_matches_metadata(target: DisposablePostgresTarget) -> None:
    differences = compare_live_schema_to_metadata(target)
    if differences:
        raise CutoverValidationError(
            "live CareerOPS schema differs from canonical MetaData: "
            f"{differences!r}"
        )


def _metadata_retains_type_probe(column_type: Any, data_type: str) -> bool:
    if data_type == "uuid":
        return isinstance(column_type, UUID)
    if data_type == "jsonb":
        return isinstance(column_type, JSONB)
    if data_type == "ARRAY":
        return isinstance(column_type, ARRAY)
    if data_type == "timestamp with time zone":
        return isinstance(column_type, TIMESTAMP) and bool(column_type.timezone)
    raise AssertionError(f"unsupported focused PostgreSQL type probe: {data_type}")


def validate_live_schema(
    fingerprint: CatalogFingerprint,
    *,
    target_metadata: MetaData = metadata,
) -> LiveSchemaSummary:
    """Check metadata-derived shape plus focused surviving legacy invariants."""

    errors: list[str] = []
    schema_tables = tuple(
        table
        for table in target_metadata.tables.values()
        if table.schema == CAREEROPS_SCHEMA
    )
    expected_tables = {table.name for table in schema_tables}
    expected_column_count = sum(len(table.columns) for table in schema_tables)
    metadata_columns = {
        (table.name, column.name): column
        for table in schema_tables
        for column in table.columns
    }
    expected_identities = {
        (table.name, column.name)
        for table in schema_tables
        for column in table.columns
        if column.identity is not None
    }
    expected_pk_tables = {
        table.name for table in schema_tables if len(table.primary_key.columns) > 0
    }
    expected_fk_tables = {
        table.name for table in schema_tables if table.foreign_keys
    }
    expected_uniques = {
        str(constraint.name)
        for table in schema_tables
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name is not None
    }
    expected_checks = {
        str(constraint.name)
        for table in schema_tables
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }
    expected_indexes = {
        str(index.name)
        for table in schema_tables
        for index in table.indexes
        if index.name is not None
    }
    expected_partial_indexes = {
        str(index.name)
        for table in schema_tables
        for index in table.indexes
        if index.name is not None
        and index.dialect_options["postgresql"].get("where") is not None
    }

    table_set = set(fingerprint.tables)
    if not fingerprint.schema_exists:
        errors.append("schema careerops is absent")
    if table_set != expected_tables:
        errors.append(
            f"tables: missing={sorted(expected_tables - table_set)!r}, "
            f"unexpected={sorted(table_set - expected_tables)!r}"
        )
    if len(fingerprint.columns) != expected_column_count:
        errors.append(
            f"expected {expected_column_count} columns, got {len(fingerprint.columns)}"
        )

    columns = {(str(row[0]), str(row[2])): row for row in fingerprint.columns}
    identities = {key for key, row in columns.items() if row[8] == "YES"}
    if identities != expected_identities:
        errors.append(
            f"identity columns: expected={sorted(expected_identities)!r}, "
            f"actual={sorted(identities)!r}"
        )
    if any(columns[key][9] != "BY DEFAULT" for key in identities):
        errors.append("an identity column is not GENERATED BY DEFAULT")
    for key, data_type in TYPE_PROBES.items():
        metadata_column = metadata_columns.get(key)
        if metadata_column is None or not _metadata_retains_type_probe(
            metadata_column.type,
            data_type,
        ):
            continue
        if key not in columns or columns[key][3] != data_type:
            errors.append(f"{key[0]}.{key[1]} is not {data_type}")
    for key in REPAIRED_NULLABLE:
        metadata_column = metadata_columns.get(key)
        if metadata_column is None or metadata_column.nullable is not True:
            continue
        if key not in columns or columns[key][6] != "YES":
            errors.append(f"0005 nullable repair missing for {key[0]}.{key[1]}")
    for key in REPAIRED_AUDIT_NOT_NULL:
        metadata_column = metadata_columns.get(key)
        if metadata_column is None or metadata_column.nullable is not False:
            continue
        if key not in columns or columns[key][6] != "NO":
            errors.append(f"0005 audit column is nullable: {key[0]}.{key[1]}")

    constraints_by_type = {
        kind: {str(row[1]) for row in fingerprint.constraints if row[2] == kind}
        for kind in ("p", "f", "u", "c")
    }
    pk_tables = {str(row[0]) for row in fingerprint.constraints if row[2] == "p"}
    fk_tables = {str(row[0]) for row in fingerprint.constraints if row[2] == "f"}
    if pk_tables != expected_pk_tables:
        errors.append(
            f"primary-key tables: expected={sorted(expected_pk_tables)!r}, "
            f"actual={sorted(pk_tables)!r}"
        )
    if not expected_fk_tables.issubset(fk_tables):
        errors.append(
            f"tables without foreign keys: "
            f"{sorted(expected_fk_tables - fk_tables)!r}"
        )
    if not expected_uniques.issubset(constraints_by_type["u"]):
        missing_uniques = sorted(expected_uniques - constraints_by_type["u"])
        errors.append(f"unique constraints missing: {missing_uniques!r}")
    if not expected_checks.issubset(constraints_by_type["c"]):
        missing_checks = sorted(expected_checks - constraints_by_type["c"])
        errors.append(f"check constraints missing: {missing_checks!r}")

    index_definitions = {str(row[1]): str(row[2]) for row in fingerprint.indexes}
    if not expected_indexes.issubset(index_definitions):
        errors.append(
            f"indexes missing: {sorted(expected_indexes - index_definitions.keys())!r}"
        )
    partial_indexes = {
        name for name, definition in index_definitions.items() if " WHERE " in definition.upper()
    }
    if partial_indexes != expected_partial_indexes:
        errors.append(
            f"partial indexes: expected={sorted(expected_partial_indexes)!r}, "
            f"actual={sorted(partial_indexes)!r}"
        )
    if errors:
        raise CutoverValidationError("; ".join(errors))

    return LiveSchemaSummary(
        tables=fingerprint.tables,
        column_count=len(fingerprint.columns),
        constraint_count=len(fingerprint.constraints),
        index_count=len(fingerprint.indexes),
        identity_columns=tuple(sorted(identities)),
        partial_indexes=tuple(sorted(partial_indexes)),
    )


def read_alembic_revision(target: DisposablePostgresTarget) -> str | None:
    with psycopg.connect(target.dsn) as connection:
        if _scalar(connection, "SELECT to_regclass('public.alembic_version')::text") is None:
            return None
        rows = _rows(
            connection,
            "SELECT version_num FROM public.alembic_version ORDER BY version_num",
        )
    if not rows:
        return None
    if len(rows) != 1:
        raise CutoverValidationError(f"expected one Alembic head row, found {len(rows)}")
    return str(rows[0][0])


def _assert_revision(
    target: DisposablePostgresTarget,
    expected_revision: str,
) -> str:
    revision = read_alembic_revision(target)
    if revision != expected_revision:
        raise CutoverValidationError(
            f"expected Alembic revision {expected_revision!r}, found {revision!r}"
        )
    return revision


def _fresh_path(target: DisposablePostgresTarget, root: Path) -> FreshPathReport:
    config = build_alembic_config(target, project_root=root)
    head_revision = get_single_alembic_head(config)
    command.upgrade(config, "head")
    _assert_revision(target, head_revision)
    first = capture_catalog_fingerprint(target)
    schema = validate_live_schema(first)
    _assert_live_schema_matches_metadata(target)

    command.upgrade(config, "head")
    second = capture_catalog_fingerprint(target)
    if second != first:
        raise CutoverValidationError("second fresh-path upgrade head was not a no-op")
    _assert_revision(target, head_revision)
    return FreshPathReport(
        head_revision=head_revision,
        schema=schema,
        catalog_sha256=first.sha256,
        second_upgrade_was_noop=True,
    )


def _migration_paths(root: Path) -> tuple[Path, ...]:
    paths = tuple(root / "sql" / "migrations" / name for name in LEGACY_MIGRATIONS)
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"legacy migrations are missing: {missing!r}")
    return paths


def apply_legacy_migrations(
    target: DisposablePostgresTarget,
    *,
    project_root: Path,
) -> tuple[str, ...]:
    paths = _migration_paths(project_root)
    with psycopg.connect(target.dsn, autocommit=True) as connection:
        for path in paths:
            connection.execute(path.read_text(encoding="utf-8"))
    return tuple(path.name for path in paths)


def _legacy_path(target: DisposablePostgresTarget, root: Path) -> LegacyPathReport:
    config = build_alembic_config(target, project_root=root)
    head_revision = get_single_alembic_head(config)
    revisions_after_baseline = get_revisions_after(config, BASELINE_REVISION)
    migrations = apply_legacy_migrations(target, project_root=root)
    if read_alembic_revision(target) is not None:
        raise CutoverValidationError("legacy schema has Alembic state before stamp")
    before_stamp = capture_catalog_fingerprint(target)

    command.stamp(config, BASELINE_REVISION)
    _assert_revision(target, BASELINE_REVISION)
    after_stamp = capture_catalog_fingerprint(target)
    if after_stamp != before_stamp:
        raise CutoverValidationError(
            "stamp mutated CareerOPS catalog: "
            f"before={before_stamp.sha256}, after={after_stamp.sha256}"
        )

    command.upgrade(config, "head")
    _assert_revision(target, head_revision)
    after_upgrade = capture_catalog_fingerprint(target)
    if not revisions_after_baseline and after_upgrade != after_stamp:
        raise CutoverValidationError(
            "upgrade head after stamp changed the CareerOPS catalog despite "
            "having no revisions after the baseline"
        )
    schema = validate_live_schema(after_upgrade)
    _assert_live_schema_matches_metadata(target)
    return LegacyPathReport(
        baseline_revision=BASELINE_REVISION,
        head_revision=head_revision,
        revisions_after_baseline=revisions_after_baseline,
        schema=schema,
        migrations=migrations,
        pre_stamp_catalog_sha256=before_stamp.sha256,
        post_upgrade_catalog_sha256=after_upgrade.sha256,
        stamp_catalog_unchanged=True,
    )


def validate_alembic_cutover(
    dsn: str,
    *,
    project_root: Path | None = None,
) -> CutoverValidationReport:
    """Run and prove both CAR-45 entry paths, then clean the managed state."""

    target = validate_disposable_postgres_dsn(dsn)
    root = PROJECT_ROOT if project_root is None else project_root.resolve()
    reset_disposable_state(target)
    try:
        assert_disposable_state_is_empty(target)
        with psycopg.connect(target.dsn) as connection:
            postgresql_version = str(_scalar(connection, "SELECT version()"))
            database = str(_scalar(connection, "SELECT current_database()"))
        if database != target.database:
            raise DisposableDatabaseError("connected database differs from the guarded DSN")

        fresh = _fresh_path(target, root)
        reset_disposable_state(target)
        assert_disposable_state_is_empty(target)
        legacy = _legacy_path(target, root)
        if legacy.head_revision != fresh.head_revision:
            raise CutoverValidationError(
                "Alembic graph head changed while cutover validation was running"
            )
        return CutoverValidationReport(
            postgresql_version=postgresql_version,
            database=database,
            host=target.host,
            baseline_revision=BASELINE_REVISION,
            head_revision=fresh.head_revision,
            fresh=fresh,
            legacy=legacy,
        )
    finally:
        reset_disposable_state(target)


def format_validation_report(report: CutoverValidationReport) -> str:
    tables = ", ".join(report.fresh.schema.tables)
    migrations = ", ".join(report.legacy.migrations)
    if report.legacy.post_stamp_upgrade_was_noop:
        legacy_upgrade_result = (
            "  alembic upgrade head after stamp: PASS "
            f"(no-op; baseline is graph head {report.legacy.head_revision}; "
            "catalog unchanged)"
        )
        revisions_after_baseline = "none"
    else:
        revisions_after_baseline = ", ".join(
            report.legacy.revisions_after_baseline
        )
        legacy_upgrade_result = (
            "  alembic upgrade head after stamp: PASS "
            f"(applied {len(report.legacy.revisions_after_baseline)} revision(s); "
            f"reached graph head {report.legacy.head_revision})"
        )
    return "\n".join(
        (
            "CAR-45 real PostgreSQL cutover validation: PASS",
            f"PostgreSQL: {report.postgresql_version}",
            f"Disposable target: {report.database} on {report.host}",
            f"Baseline revision: {report.baseline_revision}",
            f"Current graph head: {report.head_revision}",
            "Fresh path:",
            "  first alembic upgrade head: PASS "
            f"(revision {report.fresh.head_revision})",
            f"  CareerOPS tables ({len(report.fresh.schema.tables)}): {tables}",
            f"  live catalog: {report.fresh.schema.column_count} columns, "
            f"{report.fresh.schema.constraint_count} constraints, "
            f"{report.fresh.schema.index_count} indexes",
            f"  catalog fingerprint: {report.fresh.catalog_sha256}",
            "  second alembic upgrade head: PASS (no-op; catalog unchanged)",
            "Legacy path:",
            f"  migrations applied: {migrations}",
            f"  pre-stamp catalog fingerprint: {report.legacy.pre_stamp_catalog_sha256}",
            f"  alembic stamp {report.legacy.baseline_revision}: PASS",
            "  stamp DDL proof: CareerOPS catalog unchanged; baseline CREATE SCHEMA "
            "was not invoked",
            f"  revisions after baseline: {revisions_after_baseline}",
            f"  post-upgrade catalog fingerprint: "
            f"{report.legacy.post_upgrade_catalog_sha256}",
            legacy_upgrade_result,
        )
    )
