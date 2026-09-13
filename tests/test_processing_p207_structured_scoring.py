from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from support.processing import (
    HASH_A,
    HASH_B,
    HASH_C,
    HASH_D,
    empty_qualification_set,
    empty_requirement_set,
    requirement,
    requirement_set,
    score,
)

from careerops_processing.contracts import (
    DataQualityStatus,
    MatchDecision,
    NormalizedVacancy,
    RawObservationRef,
    RequirementGroupQualification,
    RequirementKind,
    RequirementQualification,
    RequirementQualificationSet,
    RequirementQualificationState,
    SourceLabel,
    SourceValue,
    SupportBounds,
    ValueState,
)
from careerops_processing.contracts.normalized import DataQualityReport, Employer, Location


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


def test_known_role_work_format_and_location_are_first_class_score_components() -> None:
    vacancy = _vacancy(
        title="Data Engineer",
        work_formats=(SourceLabel(key="office", label="Офис"),),
        location=Location(area_id="1", area_name="Москва"),
    )

    decision = score(
        empty_requirement_set(),
        empty_qualification_set(),
        filtering={
            "allowed_primary_roles": ["data_engineering"],
            "allowed_work_formats": ["onsite"],
            "allowed_area_ids": ["1"],
        },
        weights={"role_fit": "1", "work_format_fit": "1", "location_fit": "1"},
        mandatory_min_support="0",
        vacancy=vacancy,
    )

    by_key = {item.key: item for item in decision.components}
    assert by_key["role_fit"].lower == Decimal("100")
    assert by_key["work_format_fit"].lower == Decimal("100")
    assert by_key["location_fit"].lower == Decimal("100")
    assert decision.score.lower == Decimal("100")
    assert decision.decision is MatchDecision.APPLICATION_CANDIDATE


def test_unknown_role_remains_bounded_and_forces_review() -> None:
    decision = score(
        empty_requirement_set(),
        empty_qualification_set(),
        filtering={"allowed_primary_roles": ["data_engineering"]},
        weights={"role_fit": "1"},
        mandatory_min_support="0",
        vacancy=_vacancy(title=None),
    )

    role = next(item for item in decision.components if item.key == "role_fit")
    assert role.lower == Decimal("0")
    assert role.upper == Decimal("100")
    assert decision.score.lower == Decimal("0")
    assert decision.score.upper == Decimal("100")
    assert decision.decision is MatchDecision.REVIEW


def test_positive_weight_missing_signal_remains_uncertain() -> None:
    decision = score(
        empty_requirement_set(),
        empty_qualification_set(),
        weights={"role_fit": "1"},
        vacancy=None,
    )

    assert decision.decision is MatchDecision.REVIEW
    assert decision.score.lower == Decimal("0")
    assert decision.score.upper == Decimal("100")
    assert decision.components[0].key == "role_fit"
    assert decision.components[0].weight == Decimal("1")


def test_domain_requirement_is_scored_as_domain_fit() -> None:
    domain_requirement = requirement(
        "req-domain",
        "банкинг",
        kind=RequirementKind.DOMAIN,
        statement="Опыт в банковском домене",
    )
    requirements = requirement_set(domain_requirement)
    qualification = RequirementQualificationSet(
        input_fingerprint=HASH_C,
        requirement_set_sha256=HASH_B,
        resume_evidence_set_sha256=HASH_C,
        evidence_candidate_set_sha256=HASH_D,
        qualification_version="qualification-v1",
        evaluations=(
            RequirementQualification(
                requirement_id=domain_requirement.requirement_id,
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

    decision = score(
        requirements,
        qualification,
        weights={"domain_fit": "1"},
        mandatory_min_support="0",
    )

    domain = next(item for item in decision.components if item.key == "domain_fit")
    assert domain.lower == Decimal("100")
    assert domain.upper == Decimal("100")
    assert decision.decision is MatchDecision.APPLICATION_CANDIDATE
