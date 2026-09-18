from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FACADE = Path("src/careerops_application/infrastructure/hh_applicant_transport.py")


def test_vendored_applicant_tool_has_single_careerops_facade_boundary() -> None:
    offenders: list[str] = []
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        relative = path.relative_to(PROJECT_ROOT)
        text = path.read_text(encoding="utf-8")
        if "hh_applicant_tool" in text and relative != FACADE:
            offenders.append(str(relative))
    assert offenders == []


def test_application_owner_does_not_import_processing_decision_internals() -> None:
    forbidden = (
        "careerops_processing.core",
        "careerops_processing.native_decision",
        "careerops_processing.contracts.qualification",
        "careerops_processing.contracts.scoring",
        "careerops_reranker",
    )
    offenders: list[str] = []
    for path in (PROJECT_ROOT / "src" / "careerops_application").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {token}")
    assert offenders == []


def test_retired_vendor_autonomous_application_runtime_is_absent() -> None:
    removed = (
        "hh-applicant-tool/src/hh_applicant_tool/operations/apply_vacancies.py",
        "hh-applicant-tool/src/hh_applicant_tool/operations/ui.py",
        "hh-applicant-tool/src/hh_applicant_tool/ui",
        "hh-applicant-tool/start.py",
    )
    remaining = [path for path in removed if (PROJECT_ROOT / path).exists()]
    assert remaining == []

    runtime_files = (
        PROJECT_ROOT / "hh-applicant-tool" / "crontab",
        PROJECT_ROOT / "hh-applicant-tool" / "startup.sh",
    )
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in runtime_files
        if "apply-vacancies" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
