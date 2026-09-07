from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from careerops_processing.contracts.common import (
    DataQualityStatus,
    RawObservationRef,
    SourceLabel,
    SourceTextRef,
    SourceValue,
    TextBlock,
    ValueState,
)
from careerops_processing.contracts.filtering import FilterOutcome
from careerops_processing.contracts.normalized import (
    DataQualityReport,
    Employer,
    ExperienceRange,
    Location,
    NormalizedResume,
    NormalizedVacancy,
)
from careerops_processing.contracts.policy import TargetPolicy
from careerops_processing.core.filtering import evaluate_filter

HASH_A = "a" * 64
HASH_B = "b" * 64


def known(value):
    return SourceValue(state=ValueState.KNOWN, value=value)


def missing():
    return SourceValue(state=ValueState.NOT_PROVIDED)


def raw_ref() -> RawObservationRef:
    return RawObservationRef(
        raw_uri="s3://careerops-raw/test/vacancy.json",
        raw_sha256=HASH_A,
        observed_at=datetime(2026, 9, 7, 12, tzinfo=UTC),
    )


def dq() -> DataQualityReport:
    return DataQualityReport(
        status=DataQualityStatus.CLEAN,
        full_entity_available=True,
        parse_complete=True,
    )


def vacancy(
    *,
    title: str | None = "Data Engineer",
    work_formats: tuple[SourceLabel, ...] = (),
    location: Location | None = None,
    relocation_facts: tuple[str, ...] = (),
    minimum_experience: Decimal | None = None,
    archived: bool = False,
    closed: bool = False,
    text_blocks: tuple[TextBlock, ...] = (),
) -> NormalizedVacancy:
    return NormalizedVacancy(
        schema_version="careerops.hh.vacancy.normalized.v1",
        normalization_version="normalizer-v1",
        dictionary_version="dictionary-v1",
        materialization_key="vacancy:1",
        source_key="hh",
        source_entity_id="vacancy-1",
        raw=raw_ref(),
        semantic_content_hash=HASH_B,
        title=known(title) if title is not None else missing(),
        employer=known(Employer(name="Example")),
        experience=(
            known(ExperienceRange(minimum_years=minimum_experience))
            if minimum_experience is not None
            else missing()
        ),
        work_formats=work_formats,
        location=known(location) if location is not None else missing(),
        relocation_facts=relocation_facts,
        salary=missing(),
        archived=known(archived),
        closed_for_applicants=known(closed),
        published_at=missing(),
        text_blocks=text_blocks,
        dq=dq(),
    )


def resume(*, total_experience: Decimal | None) -> NormalizedResume:
    return NormalizedResume(
        schema_version="careerops.hh.resume.normalized.v1",
        normalization_version="normalizer-v1",
        dictionary_version="dictionary-v1",
        materialization_key="resume:1",
        source_key="hh",
        account_key="junior",
        source_entity_id="resume-1",
        raw=raw_ref(),
        semantic_content_hash=HASH_A,
        headline=missing(),
        about=missing(),
        location=missing(),
        work_preferences=missing(),
        relocation=missing(),
        business_trips=missing(),
        total_experience_years=(
            known(total_experience) if total_experience is not None else missing()
        ),
        dq=dq(),
    )


def policy(**filtering) -> TargetPolicy:
    return TargetPolicy.from_content(
        target_key="de",
        schema_version="careerops.target-policy.v1",
        policy_version="de-filter-v1",
        content={"filtering": {"schema_version": 1, **filtering}},
    )


def test_missing_or_ambiguous_title_is_kept() -> None:
    target = policy(allowed_primary_roles=["data_engineering"])
    assert evaluate_filter(vacancy(title=None), target).outcome is FilterOutcome.KEEP
    assert evaluate_filter(vacancy(title="Platform Engineer"), target).outcome is FilterOutcome.KEEP


def test_incidental_technology_does_not_change_primary_occupation() -> None:
    target = policy(allowed_primary_roles=["data_engineering"])
    result = evaluate_filter(vacancy(title="Data Engineer, Java / Kafka"), target)
    assert result.outcome is FilterOutcome.KEEP


def test_proven_primary_role_disjoint_is_excluded() -> None:
    target = policy(allowed_primary_roles=["data_engineering"])
    result = evaluate_filter(vacancy(title="Java Backend Developer"), target)
    assert result.outcome is FilterOutcome.EXCLUDE_PROVEN
    assert result.exclusions[0].reason_code == "filter.primary_role_disjoint"
    assert result.exclusions[0].evidence[0].source_path == "vacancy.title"


def test_mixed_title_keeps_pair_when_one_primary_role_is_allowed() -> None:
    target = policy(allowed_primary_roles=["data_engineering"])
    result = evaluate_filter(vacancy(title="Data Engineer / Java Developer"), target)
    assert result.outcome is FilterOutcome.KEEP


def test_remote_alternative_prevents_geographic_reject() -> None:
    target = policy(
        allowed_primary_roles=["data_engineering"],
        allowed_work_formats=["remote"],
        allowed_area_ids=["2"],
    )
    result = evaluate_filter(
        vacancy(
            work_formats=(SourceLabel(key="remote", label="Удаленная работа"),),
            location=Location(area_id="1", area_name="Москва"),
        ),
        target,
    )
    assert result.outcome is FilterOutcome.KEEP


def test_remote_alternative_prevents_geo_reject_without_format_restriction() -> None:
    target = policy(
        allowed_primary_roles=["data_engineering"],
        allowed_area_ids=["2"],
    )
    result = evaluate_filter(
        vacancy(
            work_formats=(SourceLabel(key="remote", label="Remote"),),
            location=Location(area_id="1", area_name="Москва"),
        ),
        target,
    )
    assert result.outcome is FilterOutcome.KEEP


def test_explicit_onsite_location_conflict_is_excluded() -> None:
    target = policy(
        allowed_work_formats=["onsite"],
        allowed_area_ids=["2"],
    )
    result = evaluate_filter(
        vacancy(
            work_formats=(SourceLabel(key="onsite", label="Офис"),),
            location=Location(area_id="1", area_name="Москва"),
        ),
        target,
    )
    assert result.outcome is FilterOutcome.EXCLUDE_PROVEN
    assert any(
        item.reason_code == "policy.hard_location_conflict"
        for item in result.exclusions
    )


def test_unknown_work_format_blocks_hard_location_exclusion() -> None:
    target = policy(allowed_area_ids=["2"])
    result = evaluate_filter(
        vacancy(
            work_formats=(SourceLabel(key="flexible", label="Гибкий формат"),),
            location=Location(area_id="1", area_name="Москва"),
        ),
        target,
    )
    assert result.outcome is FilterOutcome.KEEP


def test_known_location_without_area_identity_is_kept() -> None:
    target = policy(allowed_area_ids=["2"])
    result = evaluate_filter(
        vacancy(
            work_formats=(SourceLabel(key="onsite", label="Офис"),),
            location=Location(address="Неуточненный адрес"),
        ),
        target,
    )
    assert result.outcome is FilterOutcome.KEEP


def test_relocation_negation_is_not_a_blocker() -> None:
    target = policy(relocation_allowed=False)
    keep = evaluate_filter(
        vacancy(relocation_facts=("Relocation is not required",)),
        target,
    )
    reject = evaluate_filter(
        vacancy(relocation_facts=("Relocation required",)),
        target,
    )
    assert keep.outcome is FilterOutcome.KEEP
    assert reject.outcome is FilterOutcome.EXCLUDE_PROVEN
    assert reject.exclusions[0].reason_code == "policy.relocation_required"


def test_lead_is_not_universal_management_reject() -> None:
    target = policy(management_allowed=False)
    lead_result = evaluate_filter(vacancy(title="Lead Data Engineer"), target)
    assert lead_result.outcome is FilterOutcome.KEEP
    result = evaluate_filter(vacancy(title="Team Lead Data Engineer"), target)
    assert result.outcome is FilterOutcome.EXCLUDE_PROVEN
    assert result.exclusions[0].reason_code == "filter.management_forbidden"


def test_seniority_reject_requires_explicit_policy() -> None:
    unrestricted = policy()
    restricted = policy(forbidden_seniority=["senior"])
    item = vacancy(title="Senior Data Engineer")
    assert evaluate_filter(item, unrestricted).outcome is FilterOutcome.KEEP
    result = evaluate_filter(item, restricted)
    assert result.outcome is FilterOutcome.EXCLUDE_PROVEN
    assert result.exclusions[0].reason_code == "filter.seniority_forbidden"


def test_experience_gap_reject_requires_known_resume_fact_and_policy() -> None:
    target = policy(maximum_experience_gap_years=1)
    item = vacancy(minimum_experience=Decimal("5"))
    rejected = evaluate_filter(item, target, resume(total_experience=Decimal("2")))
    unknown = evaluate_filter(item, target, resume(total_experience=None))
    assert rejected.outcome is FilterOutcome.EXCLUDE_PROVEN
    assert rejected.exclusions[0].reason_code == "filter.experience_gap_exceeded"
    assert unknown.outcome is FilterOutcome.KEEP


def test_forbidden_context_respects_negation_and_preserves_provenance() -> None:
    target = policy(forbidden_context_terms=["gambling"])
    safe_block = TextBlock(
        block_id="b1",
        text="We do not work with gambling products.",
        ordinal=0,
        source_ref=SourceTextRef(
            source_path="description",
            locator="description[0]",
            quote="We do not work with gambling products.",
        ),
    )
    bad_block = TextBlock(
        block_id="b2",
        text="Build a gambling platform for international markets.",
        ordinal=0,
        source_ref=SourceTextRef(
            source_path="description",
            locator="description[0]",
            quote="Build a gambling platform for international markets.",
        ),
    )
    assert (
        evaluate_filter(vacancy(text_blocks=(safe_block,)), target).outcome
        is FilterOutcome.KEEP
    )
    result = evaluate_filter(vacancy(text_blocks=(bad_block,)), target)
    assert result.outcome is FilterOutcome.EXCLUDE_PROVEN
    evidence = result.exclusions[0].evidence[0]
    assert evidence.source_locator == "description[0]"
    assert evidence.quote == "Build a gambling platform for international markets."


def test_explicit_unavailable_vacancy_is_excluded() -> None:
    result = evaluate_filter(vacancy(archived=True), policy())
    assert result.outcome is FilterOutcome.EXCLUDE_PROVEN
    assert result.exclusions[0].reason_code == "source.vacancy_unavailable"


def test_multiple_work_format_alternatives_keep_if_one_is_compatible() -> None:
    target = policy(allowed_work_formats=["remote"])
    item = vacancy(
        work_formats=(
            SourceLabel(key="onsite", label="Офис"),
            SourceLabel(key="remote", label="Удаленно"),
        )
    )
    assert evaluate_filter(item, target).outcome is FilterOutcome.KEEP
