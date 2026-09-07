"""Bootstrap adversarial corpus для первого измерения high-recall фильтра"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from careerops_processing.contracts import (
    DataQualityReport,
    DataQualityStatus,
    Employer,
    ExperienceRange,
    FilterOutcome,
    Location,
    NormalizedResume,
    NormalizedVacancy,
    RawObservationRef,
    SourceLabel,
    SourceTextRef,
    SourceValue,
    TargetPolicy,
    TextBlock,
    ValueState,
    WorkPreferences,
)
from careerops_processing.infrastructure.policy_repository import TargetPolicyRepository

from .filter_gold import FilterGoldCase, FilterGoldCorpus, FilterGoldCorpusKind

_BASE_TIME = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
_SCHEMA_VERSION = "careerops.hh.vacancy.normalized.v1"
_RESUME_SCHEMA_VERSION = "careerops.hh.resume.normalized.v1"
_NORMALIZATION_VERSION = "filter-gold-bootstrap-v1"
_DICTIONARY_VERSION = "careerops-dictionary-2026-09"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _known(value: Any) -> SourceValue[Any]:
    return SourceValue(state=ValueState.KNOWN, value=value)


def _missing() -> SourceValue[Any]:
    return SourceValue(state=ValueState.NOT_PROVIDED)


def _vacancy(
    case_id: str,
    *,
    title: str | None,
    archived: bool = False,
    closed: bool = False,
    work_formats: tuple[str, ...] = (),
    area_name: str | None = None,
    area_id: str | None = None,
    relocation_facts: tuple[str, ...] = (),
    minimum_experience_years: Decimal | None = None,
    context_text: str | None = None,
) -> NormalizedVacancy:
    text_blocks: tuple[TextBlock, ...] = ()
    if context_text is not None:
        text_blocks = (
            TextBlock(
                block_id="context-1",
                text=context_text,
                ordinal=0,
                source_ref=SourceTextRef(
                    source_path="description",
                    locator="description",
                    quote=context_text,
                ),
            ),
        )

    experience = (
        _known(ExperienceRange(minimum_years=minimum_experience_years))
        if minimum_experience_years is not None
        else _missing()
    )
    location = (
        _known(Location(area_id=area_id, area_name=area_name))
        if area_id is not None or area_name is not None
        else _missing()
    )

    return NormalizedVacancy(
        schema_version=_SCHEMA_VERSION,
        normalization_version=_NORMALIZATION_VERSION,
        dictionary_version=_DICTIONARY_VERSION,
        materialization_key=f"gold:{case_id}",
        source_key="gold-bootstrap",
        source_entity_id=case_id,
        raw=RawObservationRef(
            raw_uri=f"s3://careerops-raw/_gold/{case_id}.json",
            raw_sha256=_sha(f"raw:{case_id}"),
            observed_at=_BASE_TIME,
        ),
        semantic_content_hash=_sha(f"semantic:{case_id}"),
        title=_known(title) if title is not None else _missing(),
        employer=_known(Employer(name="Gold Bootstrap Employer")),
        professional_roles=(),
        key_skills=(),
        experience=experience,
        employment=(),
        schedules=(),
        work_formats=tuple(
            SourceLabel(key=value, label=value) for value in work_formats
        ),
        location=location,
        relocation_facts=relocation_facts,
        salary=_missing(),
        archived=_known(archived),
        closed_for_applicants=_known(closed),
        published_at=_missing(),
        text_blocks=text_blocks,
        dq=DataQualityReport(
            status=DataQualityStatus.CLEAN,
            full_entity_available=True,
            parse_complete=True,
        ),
    )


def _resume(case_id: str, total_experience_years: Decimal | None) -> NormalizedResume:
    total = (
        _known(total_experience_years)
        if total_experience_years is not None
        else _missing()
    )
    return NormalizedResume(
        schema_version=_RESUME_SCHEMA_VERSION,
        normalization_version=_NORMALIZATION_VERSION,
        dictionary_version=_DICTIONARY_VERSION,
        materialization_key=f"gold-resume:{case_id}",
        source_key="gold-bootstrap",
        account_key="gold-account",
        source_entity_id=f"resume-{case_id}",
        raw=RawObservationRef(
            raw_uri=f"s3://careerops-raw/_gold/resume-{case_id}.json",
            raw_sha256=_sha(f"raw-resume:{case_id}"),
            observed_at=_BASE_TIME,
        ),
        semantic_content_hash=_sha(f"semantic-resume:{case_id}"),
        headline=_missing(),
        skill_set=(),
        about=_missing(),
        experience_entries=(),
        projects=(),
        education=(),
        languages=(),
        location=_missing(),
        work_preferences=_known(WorkPreferences()),
        relocation=_missing(),
        business_trips=_missing(),
        total_experience_years=total,
        dq=DataQualityReport(
            status=DataQualityStatus.CLEAN,
            full_entity_available=True,
            parse_complete=True,
        ),
    )


def _policy_with_filter_overrides(
    repository: TargetPolicyRepository,
    target_key: str,
    case_id: str,
    **overrides: Any,
) -> TargetPolicy:
    base = repository.get_current(target_key)
    content = base.parsed_content()
    filtering = dict(content.get("filtering", {}))
    filtering.update(overrides)
    content["filtering"] = filtering
    return TargetPolicy.from_content(
        target_key=target_key,
        schema_version=base.schema_version,
        policy_version=f"{base.policy_version}+gold-{case_id}",
        content=content,
    )


def _case(
    repository: TargetPolicyRepository,
    case_id: str,
    *,
    target_key: str,
    expected: FilterOutcome,
    title: str | None,
    expected_reason_codes: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    policy_overrides: dict[str, object] | None = None,
    resume_years: Decimal | None = None,
    include_resume: bool = False,
    **vacancy_kwargs: Any,
) -> FilterGoldCase:
    policy = (
        _policy_with_filter_overrides(
            repository,
            target_key,
            case_id,
            **(policy_overrides or {}),
        )
        if policy_overrides
        else repository.get_current(target_key)
    )
    return FilterGoldCase(
        case_id=case_id,
        expected_outcome=expected,
        expected_reason_codes=expected_reason_codes,
        vacancy=_vacancy(case_id, title=title, **vacancy_kwargs),
        target_policy=policy,
        resume=_resume(case_id, resume_years) if include_resume else None,
        tags=tags,
    )


def build_filter_bootstrap_corpus(
    repository: TargetPolicyRepository,
) -> FilterGoldCorpus:
    """Строит adversarial corpus без права использовать его как release gate"""

    keep = FilterOutcome.KEEP
    exclude = FilterOutcome.EXCLUDE_PROVEN

    cases = (
        _case(
            repository,
            "de-basic",
            target_key="data_engineer_junior",
            expected=keep,
            title="Data Engineer",
            tags=("role", "positive"),
        ),
        _case(
            repository,
            "de-incidental-java",
            target_key="data_engineer_junior",
            expected=keep,
            title="Data Engineer Java Kafka",
            tags=("role", "incidental-tech"),
        ),
        _case(
            repository,
            "ambiguous-python",
            target_key="data_engineer_junior",
            expected=keep,
            title="Python Developer",
            tags=("role", "ambiguity"),
        ),
        _case(
            repository,
            "ambiguous-java",
            target_key="data_engineer_junior",
            expected=keep,
            title="Java Developer",
            tags=("role", "ambiguity"),
        ),
        _case(
            repository,
            "ambiguous-research",
            target_key="data_engineer_junior",
            expected=keep,
            title="Research Engineer",
            tags=("role", "ambiguity"),
        ),
        _case(
            repository,
            "ambiguous-applied-scientist",
            target_key="data_engineer_junior",
            expected=keep,
            title="Applied Scientist",
            tags=("role", "ambiguity"),
        ),
        _case(
            repository,
            "ambiguous-bi",
            target_key="data_engineer_junior",
            expected=keep,
            title="BI Developer",
            tags=("role", "ambiguity"),
        ),
        _case(
            repository,
            "senior-not-universal-blocker",
            target_key="data_engineer_junior",
            expected=keep,
            title="Senior Data Engineer",
            tags=("seniority",),
        ),
        _case(
            repository,
            "lead-not-universal-blocker",
            target_key="data_engineer_junior",
            expected=keep,
            title="Lead Data Engineer",
            tags=("management",),
        ),
        _case(
            repository,
            "missing-title",
            target_key="data_engineer_junior",
            expected=keep,
            title=None,
            tags=("missing", "role"),
        ),
        _case(
            repository,
            "unknown-work-format",
            target_key="data_engineer_junior",
            expected=keep,
            title="Data Engineer",
            work_formats=("flexible",),
            tags=("work-format", "unknown"),
        ),
        _case(
            repository,
            "negated-forbidden-context",
            target_key="data_engineer_junior",
            expected=keep,
            title="Data Engineer",
            context_text="We do not work with gambling products",
            policy_overrides={"forbidden_context_terms": ["gambling"]},
            tags=("context", "negation"),
        ),
        _case(
            repository,
            "relocation-not-required",
            target_key="data_engineer_junior",
            expected=keep,
            title="Data Engineer",
            relocation_facts=("relocation not required",),
            policy_overrides={"relocation_allowed": False},
            tags=("relocation", "negation"),
        ),
        _case(
            repository,
            "remote-overrides-geo",
            target_key="data_engineer_junior",
            expected=keep,
            title="Data Engineer",
            work_formats=("remote",),
            area_name="Санкт-Петербург",
            policy_overrides={"allowed_area_names": ["Москва"]},
            tags=("location", "remote"),
        ),
        _case(
            repository,
            "cpp-explicit-allowed",
            target_key="cpp_developer_junior",
            expected=keep,
            title="C++ Engineer",
            tags=("role", "cpp"),
        ),
        _case(
            repository,
            "unknown-resume-experience",
            target_key="data_engineer_junior",
            expected=keep,
            title="Data Engineer",
            minimum_experience_years=Decimal("5"),
            include_resume=True,
            resume_years=None,
            policy_overrides={"maximum_experience_gap_years": 1},
            tags=("experience", "unknown"),
        ),
        _case(
            repository,
            "unknown-format-with-remote-policy",
            target_key="data_engineer_junior",
            expected=keep,
            title="Data Engineer",
            work_formats=("flexible",),
            policy_overrides={"allowed_work_formats": ["remote"]},
            tags=("work-format", "unknown"),
        ),
        _case(
            repository,
            "foreign-java-backend",
            target_key="data_engineer_junior",
            expected=exclude,
            title="Java Backend Developer",
            expected_reason_codes=("filter.primary_role_disjoint",),
            tags=("role", "hard-exclude"),
        ),
        _case(
            repository,
            "foreign-frontend",
            target_key="python_backend_junior",
            expected=exclude,
            title="Frontend Developer",
            expected_reason_codes=("filter.primary_role_disjoint",),
            tags=("role", "hard-exclude"),
        ),
        _case(
            repository,
            "archived",
            target_key="data_engineer_junior",
            expected=exclude,
            title="Data Engineer",
            archived=True,
            expected_reason_codes=("source.vacancy_unavailable",),
            tags=("availability",),
        ),
        _case(
            repository,
            "unnegated-forbidden-context",
            target_key="data_engineer_junior",
            expected=exclude,
            title="Data Engineer",
            context_text="We build gambling products",
            policy_overrides={"forbidden_context_terms": ["gambling"]},
            expected_reason_codes=("policy.forbidden_context",),
            tags=("context",),
        ),
        _case(
            repository,
            "relocation-required",
            target_key="data_engineer_junior",
            expected=exclude,
            title="Data Engineer",
            relocation_facts=("relocation required",),
            policy_overrides={"relocation_allowed": False},
            expected_reason_codes=("policy.relocation_required",),
            tags=("relocation",),
        ),
        _case(
            repository,
            "onsite-geo-conflict",
            target_key="data_engineer_junior",
            expected=exclude,
            title="Data Engineer",
            work_formats=("onsite",),
            area_name="Санкт-Петербург",
            policy_overrides={"allowed_area_names": ["Москва"]},
            expected_reason_codes=("policy.hard_location_conflict",),
            tags=("location",),
        ),
        _case(
            repository,
            "cpp-foreign-for-de",
            target_key="data_engineer_junior",
            expected=exclude,
            title="C++ Engineer",
            expected_reason_codes=("filter.primary_role_disjoint",),
            tags=("role", "cpp", "hard-exclude"),
        ),
        _case(
            repository,
            "onsite-vs-remote-only",
            target_key="data_engineer_junior",
            expected=exclude,
            title="Data Engineer",
            work_formats=("onsite",),
            policy_overrides={"allowed_work_formats": ["remote"]},
            expected_reason_codes=("policy.hard_work_format_conflict",),
            tags=("work-format",),
        ),
        _case(
            repository,
            "experience-gap",
            target_key="data_engineer_junior",
            expected=exclude,
            title="Data Engineer",
            minimum_experience_years=Decimal("5"),
            include_resume=True,
            resume_years=Decimal("1"),
            policy_overrides={"maximum_experience_gap_years": 1},
            expected_reason_codes=("filter.experience_gap_exceeded",),
            tags=("experience",),
        ),
    )

    return FilterGoldCorpus(
        corpus_id="filter-bootstrap-v1",
        corpus_kind=FilterGoldCorpusKind.BOOTSTRAP_ADVERSARIAL,
        release_gate_eligible=False,
        cases=cases,
    )
