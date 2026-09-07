from __future__ import annotations

from datetime import UTC, datetime

from careerops_processing.contracts import (
    DataQualityReport,
    DataQualityStatus,
    Employer,
    ExperienceRange,
    Location,
    NormalizedVacancy,
    RawObservationRef,
    SourceLabel,
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


def _vacancy(
    *,
    title: str,
    location: Location | None = None,
    work_formats: tuple[SourceLabel, ...] = (),
) -> NormalizedVacancy:
    return NormalizedVacancy(
        schema_version="careerops.hh.vacancy.normalized.v1",
        normalization_version="normalizer-v1",
        dictionary_version="dictionary-v1",
        materialization_key="vacancy:guard",
        source_key="hh",
        source_entity_id="vacancy-guard",
        raw=RawObservationRef(
            raw_uri="s3://careerops-raw/test/vacancy-guard.json",
            raw_sha256=HASH_A,
            observed_at=datetime(2026, 9, 7, 12, tzinfo=UTC),
        ),
        semantic_content_hash=HASH_B,
        title=_known(title),
        employer=_known(Employer(name="Example")),
        experience=_known(ExperienceRange(minimum_years=0)),
        work_formats=work_formats,
        location=_known(location) if location is not None else _missing(),
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


def _policy(**filtering) -> TargetPolicy:
    return TargetPolicy.from_content(
        target_key="guard",
        schema_version="careerops.target-policy.v1",
        policy_version="guard-filter-v1",
        content={"filtering": {"schema_version": 1, **filtering}},
    )


def test_java_backend_title_is_a_proven_foreign_occupation() -> None:
    result = evaluate_filter(
        _vacancy(title="Java Backend Developer"),
        _policy(allowed_primary_roles=["data_engineering"]),
    )
    assert result.outcome is FilterOutcome.EXCLUDE_PROVEN
    assert result.exclusions[0].reason_code == "filter.primary_role_disjoint"


def test_incidental_java_does_not_override_data_engineering_title() -> None:
    result = evaluate_filter(
        _vacancy(title="Data Engineer, Java / Kafka"),
        _policy(allowed_primary_roles=["data_engineering"]),
    )
    assert result.outcome is FilterOutcome.KEEP


def test_cpp_token_boundary_recognizes_cpp_occupation() -> None:
    foreign = evaluate_filter(
        _vacancy(title="Senior C++ Engineer"),
        _policy(allowed_primary_roles=["data_engineering"]),
    )
    compatible = evaluate_filter(
        _vacancy(title="Senior C++ Engineer"),
        _policy(allowed_primary_roles=["cpp"]),
    )
    assert foreign.outcome is FilterOutcome.EXCLUDE_PROVEN
    assert compatible.outcome is FilterOutcome.KEEP


def test_location_id_policy_does_not_reject_name_only_vacancy() -> None:
    result = evaluate_filter(
        _vacancy(
            title="Data Engineer",
            work_formats=(SourceLabel(key="onsite", label="Офис"),),
            location=Location(area_name="Москва"),
        ),
        _policy(allowed_area_ids=["2"]),
    )
    assert result.outcome is FilterOutcome.KEEP


def test_location_name_policy_does_not_reject_id_only_vacancy() -> None:
    result = evaluate_filter(
        _vacancy(
            title="Data Engineer",
            work_formats=(SourceLabel(key="onsite", label="Офис"),),
            location=Location(area_id="1"),
        ),
        _policy(allowed_area_names=["Санкт-Петербург"]),
    )
    assert result.outcome is FilterOutcome.KEEP


def test_location_reject_requires_all_configured_dimensions_to_be_comparable() -> None:
    result = evaluate_filter(
        _vacancy(
            title="Data Engineer",
            work_formats=(SourceLabel(key="onsite", label="Офис"),),
            location=Location(area_id="1"),
        ),
        _policy(
            allowed_area_ids=["2"],
            allowed_area_names=["Санкт-Петербург"],
        ),
    )
    assert result.outcome is FilterOutcome.KEEP


def test_location_id_mismatch_is_rejected_when_id_is_the_only_constraint() -> None:
    result = evaluate_filter(
        _vacancy(
            title="Data Engineer",
            work_formats=(SourceLabel(key="onsite", label="Офис"),),
            location=Location(area_id="1"),
        ),
        _policy(allowed_area_ids=["2"]),
    )
    assert result.outcome is FilterOutcome.EXCLUDE_PROVEN
    assert any(
        exclusion.reason_code == "policy.hard_location_conflict"
        for exclusion in result.exclusions
    )
