from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

REMOVED_RUNTIME_PATHS = (
    "src/careerops_scheduler",
    "src/careerops_etl",
    "src/careerops_contracts",
    "src/careerops_storage/alembic_cutover.py",
    "src/careerops_storage/postgres.py",
    "src/careerops_storage/schema.py",
    "src/careerops_storage/legacy",
    "src/careerops_integrations/hh/application_audit.py",
    "src/careerops_integrations/hh/application_claims.py",
    "src/careerops_integrations/hh/application_cli.py",
    "src/careerops_integrations/hh/apply_batch.py",
    "src/careerops_integrations/hh/batch_cli.py",
    "src/careerops_integrations/hh/cli.py",
    "src/careerops_integrations/hh/cover_letters.py",
    "src/careerops_integrations/hh/filtering.py",
    "src/careerops_integrations/hh/mapper.py",
    "src/careerops_integrations/hh/models.py",
    "src/careerops_integrations/hh/observe.py",
    "src/careerops_integrations/hh/raw.py",
    "src/careerops_integrations/hh/reader.py",
    "src/careerops_integrations/hh/resume_sync.py",
    "src/careerops_integrations/hh/runtime.py",
    "src/careerops_integrations/hh/sync.py",
    "src/careerops_integrations/hh/test_bridge.py",
    "infra/compose/hh-worker",
    "infra/systemd",
    "scripts",
    "letter.example.txt",
)

FORBIDDEN_RUNTIME_IMPORTS = (
    "careerops_integrations.hh.filtering",
    "careerops_integrations.hh.mapper",
    "careerops_integrations.hh.observe",
    "careerops_integrations.hh.runtime",
    "careerops_etl.hh_s3_to_postgres",
    "careerops_storage.alembic_cutover",
    "careerops_storage.postgres",
    "careerops_storage.schema",
    "careerops_scheduler",
)

FORBIDDEN_SOURCE_TOKENS = (
    "HHExternalWriteGuard",
    "RuntimeMode.APPLY",
    "RuntimeMode.OBSERVE",
    "submit_application(",
    "submit_application_with_test(",
)


def test_retired_runtime_paths_are_absent() -> None:
    remaining = [path for path in REMOVED_RUNTIME_PATHS if (PROJECT_ROOT / path).exists()]
    assert remaining == []


def test_source_tree_has_no_retired_runtime_imports_or_write_api() -> None:
    offenders: list[str] = []
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in (*FORBIDDEN_RUNTIME_IMPORTS, *FORBIDDEN_SOURCE_TOKENS):
            if token in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {token}")
    assert offenders == []


def test_main_tests_do_not_import_retired_runtime_modules() -> None:
    offenders: list[str] = []
    for path in (PROJECT_ROOT / "tests").glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_RUNTIME_IMPORTS:
            if token in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {token}")
    assert offenders == []


def test_vendored_hh_applicant_tool_is_preserved() -> None:
    vendor_root = PROJECT_ROOT / "hh-applicant-tool"
    assert vendor_root.is_dir()
    assert (vendor_root / "pyproject.toml").is_file()
