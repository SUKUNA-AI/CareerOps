from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from careerops_processing.contracts import NormalizedResume, NormalizedVacancy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = PROJECT_ROOT / "services" / "careerops-normalizer-spark"
NORMALIZER = SERVICE_ROOT / "normalizer.py"
RUNNER = SERVICE_ROOT / "runner.py"
RAW_STORAGE_PREFIX = "_lab/hh"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _modules() -> tuple[ModuleType, ModuleType]:
    normalizer = _load("normalizer", NORMALIZER)
    runner = _load("careerops_normalizer_runner", RUNNER)
    return normalizer, runner


def _key(kind: str, entity: str) -> str:
    logical = (
        f"v2/{kind}/date=2026-09-23/account=primary/profile=resume-main/"
        f"{kind}_id={entity}/observed_at=20260923T100000.000000Z/"
        "observation_id=11111111-1111-1111-1111-111111111111.json"
    )
    return f"{RAW_STORAGE_PREFIX}/{logical}"


def _vacancy_result(normalizer: ModuleType, runner: ModuleType, entity: str = "vac-1") -> object:
    raw = {
        "id": entity,
        "name": "Data Engineer",
        "description": "Spark, PostgreSQL and Kafka pipelines",
        "employer": {"id": "emp-1", "name": "Example"},
        "professional_roles": [{"id": "165", "name": "Data Engineer"}],
        "key_skills": [{"name": "Spark"}, {"name": "PostgreSQL"}],
        "experience": {"id": "between1And3", "name": "1–3 years"},
        "employment": {"id": "full", "name": "Full time"},
        "schedule": {"id": "remote", "name": "Remote"},
        "area": {"id": "1", "name": "Moscow"},
        "salary": {"from": 150000, "to": 250000, "currency": "RUR", "gross": False},
        "archived": False,
        "published_at": "2026-09-23T09:00:00+03:00",
    }
    body = normalizer.canonical_json_bytes(raw)
    return runner.normalize_physical_raw_object(
        _key("vacancy", entity),
        body,
        {"sha256": normalizer.sha256_bytes(body)},
        raw_bucket="careerops-raw",
        storage_prefix=RAW_STORAGE_PREFIX,
    )


def test_vacancy_normalization_matches_processing_contract() -> None:
    normalizer, runner = _modules()
    result = _vacancy_result(normalizer, runner)

    contract = NormalizedVacancy.model_validate_json(result.payload_json)
    assert contract.source_entity_id == "vac-1"
    assert contract.raw.raw_uri.startswith("s3://careerops-raw/_lab/hh/v2/vacancy/")
    assert contract.raw.raw_sha256 == result.raw_sha256
    assert contract.dq.processing_ready is True
    assert result.normalized_key.endswith("spark-normalizer-v1.json")


def test_resume_normalization_matches_processing_contract() -> None:
    normalizer, runner = _modules()
    raw = {
        "id": "resume-1",
        "title": "Data Engineer",
        "skills": "Build data pipelines and backend services",
        "skill_set": ["Spark", "Scala", "PostgreSQL"],
        "area": {"id": "1", "name": "Moscow"},
        "experience": [
            {
                "id": "exp-1",
                "company": "Example",
                "position": "Data Engineer",
                "start": "2024-01-01",
                "end": None,
                "description": "Built Spark pipelines",
            }
        ],
        "education": {
            "primary": [
                {"id": "edu-1", "name": "University", "result": "Engineering", "year": 2027}
            ]
        },
        "language": [{"name": "Russian", "level": {"id": "l1.native", "name": "Native"}}],
        "total_experience": {"months": 32},
    }
    body = normalizer.canonical_json_bytes(raw)
    result = runner.normalize_physical_raw_object(
        _key("resume", "resume-1"),
        body,
        {"sha256": normalizer.sha256_bytes(body)},
        raw_bucket="careerops-raw",
        storage_prefix=RAW_STORAGE_PREFIX,
    )

    contract = NormalizedResume.model_validate_json(result.payload_json)
    assert contract.source_entity_id == "resume-1"
    assert contract.account_key == "primary"
    assert contract.raw.raw_uri.startswith("s3://careerops-raw/_lab/hh/v2/resume/")
    assert contract.experience_entries[0].currently_active.value is True
    assert contract.dq.processing_ready is True


def test_parquet_snapshot_identity_is_order_independent() -> None:
    normalizer, runner = _modules()
    first = _vacancy_result(normalizer, runner, "vac-1")
    second = _vacancy_result(normalizer, runner, "vac-2")

    forward = runner.parquet_batch_fingerprint([first, second])
    reverse = runner.parquet_batch_fingerprint([second, first])

    assert forward == reverse
    assert len(forward) == 64


def test_parquet_rows_keep_normalized_payload_and_provenance() -> None:
    normalizer, runner = _modules()
    result = _vacancy_result(normalizer, runner)

    [row] = runner.parquet_rows([result])

    assert row["entity_type"] == "vacancy"
    assert row["source_entity_id"] == "vac-1"
    assert row["raw_sha256"] == result.raw_sha256
    assert row["normalized_sha256"] == result.normalized_sha256
    assert row["payload_json"] == result.payload_json
    NormalizedVacancy.model_validate_json(row["payload_json"])


def test_physical_prefix_is_fail_closed() -> None:
    _normalizer, runner = _modules()
    try:
        runner.logical_raw_key("other/v2/vacancy/file.json", storage_prefix=RAW_STORAGE_PREFIX)
    except ValueError as exc:
        assert "outside configured storage prefix" in str(exc)
    else:
        raise AssertionError("normalizer accepted a RAW object outside configured prefix")
