from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from careerops_processing.contracts import (
    DataQualityStatus,
    MatchDecision,
    NormalizedVacancy,
    RawObservationRef,
    Requirement,
    RequirementContext,
    RequirementGroup,
    RequirementGroupOperator,
    RequirementGroupQualification,
    RequirementImportance,
    RequirementKind,
    RequirementModality,
    RequirementQualification,
    RequirementQualificationSet,
    RequirementQualificationState,
    RequirementSet,
    SemanticPolarity,
    SemanticSourceRef,
    SemanticSubject,
    SourceLabel,
    SourceValue,
    SupportBounds,
    TargetPolicy,
    ValueState,
)
from careerops_processing.contracts.normalized import DataQualityReport, Employer, Location
from careerops_processing.core import score_match

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _known(value):
    return SourceValue(state=ValueState.KNOWN, value=value)


def _missing():
    return SourceValue(state=ValueState.NOT_PROVIDED)


def _vacancy(
    *,
    title: str | None,
    work_formats: tuple[SourceLabel, ...] = (),
    location: Location | None = None,
) -> NormalizedVacancy:
    return NormalizedVacancy.model_construct(
        title=_known(title) if title is not None else _missing(),
        work_formats=work_formats,
        location=_known(location) if location is not None else _missing(),
        employer=_known(Employer(name="Example")),
        dq=DataQualityReport(
            status=DataQualityStatus.CLEAN,
            full_entity_available=True,
            parse_complete=True,
        ),
        raw=RawObservationRef(
            raw_uri="s3://careerops-raw/test/vacancy.json",
            raw_sha256=HASH_A,
            observed_at=datetime(2026, 9, 10, 10, tzinfo=UTC),
        ),
    )


def _empty_requirements() -> RequirementSet:
    return RequirementSet(
        source_key="hh",
        source_entity_id="vacancy-1",
        semantic_content_hash=HASH_A,
        normalized_schema_version="vacancy-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dictionary-v1",
        extraction_version="requirements-v2",
    )


def _empty_qualification() -> RequirementQualificationSet:
    return RequirementQualificationSet(
        input_fingerprint=HASH_A,
        requirement_set_sha256=HASH_B,
        resume_evidence_set_sha256=HASH_C,
        evidence_candidate_set_sha256=HASH_D,
        qualification_version="qualification-v1",
    )


def _policy(*, filtering: dict[str, object], weights: dict[str, str]) -> TargetPolicy:
    return TargetPolicy.from_content(
        target_key="de",
        schema_version="target-policy-v1",
        policy_version="policy-v1",
        content={
            "filtering": {"schema_version": 1, **filtering},
            "scoring": {
                "schema_version": 1,
                "calibration_version": "gold-v1",
                "candidate_min_score": "80",
                "mandatory_min_support": "0",
                "component_weights": weights,
                "candidate_ttl_seconds": 3600,
            },
        },
    )


def test_known_role_work_format_and_location_are_first_class_score_components() -> None:
    vacancy = _vacancy(
        title="Data Engineer",
        work_formats=(SourceLabel(key="office", label="Офис"),),
        location=Location(area_id="1", area_name="Москва"),
    )
    policy = _policy(
        filtering={
            "allowed_primary_roles": ["data_engineering"],
            "allowed_work_formats": ["onsite"],
            "allowed_area_ids": ["1"],
        },
        weights={"role_fit": "1", "work_format_fit": "1", "location_fit": "1"},
    )

    decision = score_match(
        input_fingerprint=HASH_A,
        qualification_set_ref_sha256=HASH_B,
        requirement_set=_empty_requirements(),
        qualification_set=_empty_qualification(),
        target_policy=policy,
        scoring_version="scoring-v1",
        calibration_version="gold-v1",
        vacancy=vacancy,
    )

    by_key = {item.key: item for item in decision.components}
    assert by_key["role_fit"].lower == Decimal("100")
    assert by_key["work_format_fit"].lower == Decimal("100")
    assert by_key["location_fit"].lower == Decimal("100")
    assert decision.score.lower == Decimal("100")
    assert decision.decision is MatchDecision.APPLICATION_CANDIDATE


def test_unknown_role_remains_bounded_and_forces_review_when_outcome_can_change() -> None:
    policy = _policy(
        filtering={"allowed_primary_roles": ["data_engineering"]},
        weights={"role_fit": "1"},
    )

    decision = score_match(
        input_fingerprint=HASH_A,
        qualification_set_ref_sha256=HASH_B,
        requirement_set=_empty_requirements(),
        qualification_set=_empty_qualification(),
        target_policy=policy,
        scoring_version="scoring-v1",
        calibration_version="gold-v1",
        vacancy=_vacancy(title=None),
    )

    role = next(item for item in decision.components if item.key == "role_fit")
    assert role.lower == Decimal("0")
    assert role.upper == Decimal("100")
    assert decision.score.lower == Decimal("0")
    assert decision.score.upper == Decimal("100")
    assert decision.decision is MatchDecision.REVIEW


def test_domain_requirement_is_scored_as_domain_fit() -> None:
    requirement = Requirement(
        requirement_id="req-domain",
        kind=RequirementKind.DOMAIN,
        statement="Опыт в банковском домене",
        subjects=(SemanticSubject(text="банкинг", normalized="банкинг"),),
        context=RequirementContext.QUALIFICATION,
        importance=RequirementImportance.MANDATORY,
        modality=RequirementModality.REQUIRED,
        polarity=SemanticPolarity.POSITIVE,
        source_refs=(
            SemanticSourceRef(source_path="requirements.domain", rendered_value="банкинг"),
        ),
    )
    requirement_set = RequirementSet(
        source_key="hh",
        source_entity_id="vacancy-1",
        semantic_content_hash=HASH_A,
        normalized_schema_version="vacancy-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dictionary-v1",
        extraction_version="requirements-v2",
        requirements=(requirement,),
        groups=(
            RequirementGroup(
                group_id="root",
                operator=RequirementGroupOperator.ALL,
                requirement_ids=(requirement.requirement_id,),
            ),
        ),
        root_group_id="root",
    )
    qualification = RequirementQualificationSet(
        input_fingerprint=HASH_A,
        requirement_set_sha256=HASH_B,
        resume_evidence_set_sha256=HASH_C,
        evidence_candidate_set_sha256=HASH_D,
        qualification_version="qualification-v1",
        evaluations=(
            RequirementQualification(
                requirement_id=requirement.requirement_id,
                state=RequirementQualificationState.MATCHED,
                support=SupportBounds(lower=Decimal("1"), upper=Decimal("1")),
                selection_complete=True,
                ranked_evidence_ids=("ev-domain",),
                supporting_evidence_ids=("ev-domain",),
                reason_codes=("requirements.direct_support",),
            ),
        ),
        groups=(
            RequirementGroupQualification(
                group_id="root",
                support=SupportBounds(lower=Decimal("1"), upper=Decimal("1")),
            ),
        ),
    )
    policy = _policy(filtering={}, weights={"domain_fit": "1"})

    decision = score_match(
        input_fingerprint=HASH_A,
        qualification_set_ref_sha256=HASH_B,
        requirement_set=requirement_set,
        qualification_set=qualification,
        target_policy=policy,
        scoring_version="scoring-v1",
        calibration_version="gold-v1",
    )

    domain = next(item for item in decision.components if item.key == "domain_fit")
    assert domain.lower == Decimal("100")
    assert domain.upper == Decimal("100")
    assert decision.decision is MatchDecision.APPLICATION_CANDIDATE
