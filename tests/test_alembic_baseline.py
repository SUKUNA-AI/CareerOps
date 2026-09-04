from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql

from careerops_storage.schema import CAREEROPS_SCHEMA, metadata

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = PROJECT_ROOT / "alembic.ini"
BASELINE_REVISION = "20260904_0005"
POSTGRESQL_DIALECT = postgresql.dialect()

FORBIDDEN_V2_TABLES = {
    "search_query_states",
    "search_page_tasks",
    "vacancy_processing",
    "vacancy_filter_results",
    "resume_snapshots",
    "resume_evidence",
    "model_runs",
    "vacancy_extractions",
    "vacancy_requirements",
    "rerank_runs",
    "rerank_evidence_matches",
    "vacancy_resume_matches",
    "application_candidates",
}


def _config() -> Config:
    return Config(str(ALEMBIC_CONFIG))


def _baseline_module() -> ModuleType:
    script = ScriptDirectory.from_config(_config())
    revision = script.get_revision(BASELINE_REVISION)
    assert revision is not None
    return revision.module


def _normalized_sql(value: object) -> str:
    return " ".join(str(value).split())


def _compiled_expression(value: object, table_name: str) -> str:
    if isinstance(value, str):
        sql = value
    else:
        sql = str(value.compile(dialect=POSTGRESQL_DIALECT))  # type: ignore[attr-defined]
    for prefix in (f"{CAREEROPS_SCHEMA}.{table_name}.", f"{table_name}."):
        sql = sql.replace(prefix, "")
    return _normalized_sql(sql)


def _table_signature(table: sa.Table) -> dict[str, object]:
    columns = []
    for column in table.columns:
        identity = column.identity
        default = None
        if identity is None and column.server_default is not None:
            default = _normalized_sql(column.server_default.arg)
        columns.append(
            (
                column.name,
                str(column.type.compile(dialect=POSTGRESQL_DIALECT)),
                column.nullable,
                default,
                None if identity is None else (identity.always, identity.start, identity.increment),
            )
        )

    checks = sorted(
        (
            str(constraint.name),
            _normalized_sql(constraint.sqltext),
        )
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    )
    unique_constraints = sorted(
        (
            str(constraint.name),
            tuple(constraint.columns.keys()),
        )
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    )
    foreign_keys = sorted(
        (
            constraint.name,
            tuple(constraint.column_keys),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.foreign_key_constraints
    )
    return {
        "columns": tuple(columns),
        "primary_key": tuple(table.primary_key.columns.keys()),
        "checks": tuple(checks),
        "unique_constraints": tuple(unique_constraints),
        "foreign_keys": tuple(foreign_keys),
    }


@dataclass(frozen=True)
class RecordedIndex:
    table_key: str
    expressions: tuple[str, ...]
    unique: bool
    postgresql_where: str | None


class UpgradeRecorder:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.indexes: dict[str, RecordedIndex] = {}
        self.statements: list[object] = []

    def execute(self, statement: object) -> None:
        self.statements.append(statement)

    def create_table(
        self,
        name: str,
        *elements: Any,
        schema: str | None = None,
    ) -> sa.Table:
        return sa.Table(name, self.metadata, *elements, schema=schema)

    def create_index(
        self,
        name: str,
        table_name: str,
        columns: list[object],
        *,
        unique: bool,
        schema: str,
        **dialect_options: object,
    ) -> None:
        where = dialect_options.pop("postgresql_where", None)
        assert not dialect_options
        self.indexes[name] = RecordedIndex(
            table_key=f"{schema}.{table_name}",
            expressions=tuple(_compiled_expression(column, table_name) for column in columns),
            unique=unique,
            postgresql_where=(None if where is None else _compiled_expression(where, table_name)),
        )


class DowngradeRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def drop_index(
        self,
        name: str,
        *,
        table_name: str,
        schema: str,
    ) -> None:
        self.calls.append(("index", (schema, table_name, name)))

    def drop_table(self, name: str, *, schema: str) -> None:
        self.calls.append(("table", f"{schema}.{name}"))

    def execute(self, statement: object) -> None:
        self.calls.append(("statement", statement))


def _canonical_indexes() -> dict[str, RecordedIndex]:
    result = {}
    for table in metadata.tables.values():
        for index in table.indexes:
            assert index.name is not None
            where = index.dialect_options["postgresql"]["where"]
            result[index.name] = RecordedIndex(
                table_key=table.key,
                expressions=tuple(
                    _compiled_expression(expression, table.name) for expression in index.expressions
                ),
                unique=bool(index.unique),
                postgresql_where=(
                    None if where is None else _compiled_expression(where, table.name)
                ),
            )
    return result


def test_alembic_dependency_and_safe_project_configuration() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "alembic>=1.19,<2" in project["project"]["dependencies"]

    config = _config()
    script_location = config.get_main_option("script_location")
    assert script_location is not None
    assert Path(script_location).resolve() == (PROJECT_ROOT / "alembic").resolve()
    assert config.get_main_option("sqlalchemy.url") == ""
    assert config.get_main_option("prepend_sys_path") is None


def test_migration_graph_has_one_canonical_baseline() -> None:
    script = ScriptDirectory.from_config(_config())
    assert script.get_bases() == [BASELINE_REVISION]
    assert script.get_heads() == [BASELINE_REVISION]

    revision = script.get_revision(BASELINE_REVISION)
    assert revision is not None
    assert revision.down_revision is None
    assert not revision.branch_labels


def test_baseline_upgrade_matches_canonical_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _baseline_module()
    recorder = UpgradeRecorder()
    monkeypatch.setattr(baseline, "op", recorder)

    baseline.upgrade()

    assert len(recorder.statements) == 1
    create_schema = recorder.statements[0]
    assert isinstance(create_schema, sa.schema.CreateSchema)
    assert create_schema.element == CAREEROPS_SCHEMA

    assert set(recorder.metadata.tables) == set(metadata.tables)
    for table_key, canonical_table in metadata.tables.items():
        assert _table_signature(recorder.metadata.tables[table_key]) == _table_signature(
            canonical_table
        )
    assert recorder.indexes == _canonical_indexes()


def test_baseline_downgrade_removes_indexes_tables_and_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _baseline_module()
    recorder = DowngradeRecorder()
    monkeypatch.setattr(baseline, "op", recorder)

    baseline.downgrade()

    dropped_indexes = {value[2] for kind, value in recorder.calls if kind == "index"}
    dropped_tables = {value for kind, value in recorder.calls if kind == "table"}
    assert dropped_indexes == set(_canonical_indexes())
    assert dropped_tables == set(metadata.tables)

    first_table = next(index for index, call in enumerate(recorder.calls) if call[0] == "table")
    assert all(kind == "index" for kind, _ in recorder.calls[:first_table])
    assert recorder.calls[-1][0] == "statement"
    drop_schema = recorder.calls[-1][1]
    assert isinstance(drop_schema, sa.schema.DropSchema)
    assert drop_schema.element == CAREEROPS_SCHEMA


def test_offline_upgrade_works_outside_checkout_from_installed_package(
    workspace_tmp_dir: Path,
) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["CAREEROPS_POSTGRES_DSN"] = "postgresql://test_user@127.0.0.1:5432/careerops_test"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_CONFIG),
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=workspace_tmp_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    sql = completed.stdout
    assert sql.count("CREATE SCHEMA careerops;") == 1
    assert sql.count("CREATE TABLE careerops.") == len(metadata.tables)
    assert sql.count("CREATE INDEX") + sql.count("CREATE UNIQUE INDEX") == len(_canonical_indexes())
    assert BASELINE_REVISION in sql
    assert all(table_name not in sql for table_name in FORBIDDEN_V2_TABLES)
    assert "0001_create_oltp_core.sql" not in sql
    assert "0005_repair_legacy_oltp_schema.sql" not in sql


def test_missing_database_url_fails_without_attempting_a_connection(
    workspace_tmp_dir: Path,
) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("CAREEROPS_POSTGRES_DSN", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_CONFIG),
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=workspace_tmp_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "Set CAREEROPS_POSTGRES_DSN or provide sqlalchemy.url" in completed.stderr
