from __future__ import annotations

from datetime import UTC, datetime

import pytest

from careerops_processing.contracts import (
    DataQualityReport,
    DataQualityStatus,
    Employer,
    NormalizedVacancy,
    RawObservationRef,
    SourceValue,
    TargetPolicy,
    ValueState,
)
from careerops_processing.contracts.filtering import FilterOutcome
from careerops_processing.core import evaluate_filter

HASH_A = "a" * 64
HASH_B = "b" * 64


def _known(value):
    return SourceValue(state=ValueState.KNOWN, value=value)


def _missing():
    return SourceValue(state=ValueState.NOT_PROVIDED)


def _vacancy(title: str) -> NormalizedVacancy:
    return NormalizedVacancy(
        schema_version="careerops.hh.vacancy.normalized.v1",
        normalization_version="normalizer-v1",
        dictionary_version="dictionary-v1",
        materialization_key="vacancy:ambiguity",
        source_key="hh",
        source_entity_id="vacancy-ambiguity",
        raw=RawObservationRef(
            raw_uri="s3://careerops-raw/test/vacancy-ambiguity.json",
            raw_sha256=HASH_A,
            observed_at=datetime(2026, 9, 7, 12, tzinfo=UTC),
        ),
        semantic_content_hash=HASH_B,
        title=_known(title),
        employer=_known(Employer(name="Example")),
        experience=_missing(),
        location=_missing(),
        salary=_missing(),
        archived=_known(False),
        closed_for_applicants=_known(False),
        published_at=_missing(),
        dq=DataQualityReport(
            status=DataQualityStatus.CLEAN,
            full_entity_available=True,
            parse_complete=True,
        ),
    )


def _policy(*, role: str) -> TargetPolicy:
    return TargetPolicy.from_content(
        target_key="guard",
        schema_version="careerops.target-policy.v1",
        policy_version="ambiguity-guard-v1",
        content={
            "filtering": {
                "schema_version": 1,
                "allowed_primary_roles": [role],
            }
        },
    )


@pytest.mark.parametrize(
    "title",
    [
        "Python Developer",
        "Python Software Engineer",
        "Java Developer",
        "Java Engineer",
        "Research Engineer",
        "Applied Scientist",
        "BI Developer",
    ],
)
def test_ambiguous_titles_cannot_prove_foreign_occupation(title: str) -> None:
    result = evaluate_filter(_vacancy(title), _policy(role="data_engineering"))
    assert result.outcome is FilterOutcome.KEEP


@pytest.mark.parametrize(
    ("title", "role"),
    [
        ("Python Backend Developer", "python_backend"),
        ("Java Backend Developer", "java_backend"),
        ("ML Research Engineer", "ml_research"),
        ("Data Analyst", "data_analytics"),
    ],
)
def test_explicit_titles_still_prove_their_occupation(title: str, role: str) -> None:
    compatible = evaluate_filter(_vacancy(title), _policy(role=role))
    foreign = evaluate_filter(_vacancy(title), _policy(role="cpp"))
    assert compatible.outcome is FilterOutcome.KEEP
    assert foreign.outcome is FilterOutcome.EXCLUDE_PROVEN
