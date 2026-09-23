from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from careerops_processing.contracts import NormalizedResume, NormalizedVacancy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMALIZER = PROJECT_ROOT / "services" / "careerops-normalizer-spark" / "normalizer.py"


def _module() -> ModuleType:
    name = "careerops_normalizer_spark"
    spec = importlib.util.spec_from_file_location(name, NORMALIZER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _key(kind: str, entity: str) -> str:
    return (
        f"v2/{kind}/date=2026-09-23/account=primary/profile=resume-main/"
        f"{kind}_id={entity}/observed_at=20260923T100000.000000Z/"
        "observation_id=11111111-1111-1111-1111-111111111111.json"
    )


def test_vacancy_normalization_matches_processing_contract() -> None:
    normalizer = _module()
    raw = {
        "id": "vac-1",
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
    result = normalizer.normalize_raw_object(
        _key("vacancy", "vac-1"),
        body,
        {"sha256": normalizer.sha256_bytes(body)},
        "careerops-raw",
    )

    contract = NormalizedVacancy.model_validate_json(result.payload_json)
    assert contract.source_entity_id == "vac-1"
    assert contract.raw.raw_sha256 == normalizer.sha256_bytes(body)
    assert contract.dq.processing_ready is True
    assert result.normalized_key.endswith("spark-normalizer-v1.json")


def test_resume_normalization_matches_processing_contract() -> None:
    normalizer = _module()
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
    result = normalizer.normalize_raw_object(
        _key("resume", "resume-1"),
        body,
        {"sha256": normalizer.sha256_bytes(body)},
        "careerops-raw",
    )

    contract = NormalizedResume.model_validate_json(result.payload_json)
    assert contract.source_entity_id == "resume-1"
    assert contract.account_key == "primary"
    assert contract.experience_entries[0].currently_active.value is True
    assert contract.dq.processing_ready is True
