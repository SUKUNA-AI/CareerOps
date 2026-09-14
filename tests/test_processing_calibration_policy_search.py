from __future__ import annotations

from decimal import Decimal

from careerops_processing.calibration import (
    AstraAnnotation,
    AstraDecision,
    P207PolicyCandidate,
    P207ReplayCase,
    search_p207_policy,
)
from careerops_processing.calibration.models import (
    AstraRequirement,
    AstraRequirementStatus,
    DataSufficiency,
    RequirementImportanceLabel,
    ScoreInterval,
    SupportInterval,
)


def _annotation(pair_id: str, decision: AstraDecision) -> AstraAnnotation:
    return AstraAnnotation(
        pair_id=pair_id,
        annotation_policy_version="careerops-high-recall-v1",
        annotation_source="astra",
        annotator_model=None,
        decision=decision,
        confidence="MEDIUM",
        data_sufficiency=DataSufficiency.PARTIAL,
        role_fit=3,
        seniority_fit=3,
        requirements=(
            AstraRequirement(
                requirement_text="Python",
                importance=RequirementImportanceLabel.MANDATORY,
                status=AstraRequirementStatus.UNKNOWN,
                evidence=(),
            ),
        ),
        hard_reject_reasons=(),
        reason_codes=(),
        overall_reason="fixture",
    )


def test_search_p207_policy_prioritizes_candidates_meeting_recall_floor() -> None:
    annotations = (
        _annotation("apply-1", AstraDecision.APPLICATION_CANDIDATE),
        _annotation("apply-2", AstraDecision.APPLICATION_CANDIDATE),
        _annotation("review", AstraDecision.REVIEW),
    )
    replay_cases = (
        P207ReplayCase(
            pair_id="apply-1",
            components={"fit": ScoreInterval(lower=90, upper=90)},
            mandatory_support=SupportInterval(lower=Decimal("1"), upper=Decimal("1")),
        ),
        P207ReplayCase(
            pair_id="apply-2",
            components={"fit": ScoreInterval(lower=50, upper=50)},
            mandatory_support=SupportInterval(lower=Decimal("1"), upper=Decimal("1")),
        ),
        P207ReplayCase(
            pair_id="review",
            components={"fit": ScoreInterval(lower=60, upper=60)},
            mandatory_support=SupportInterval(lower=Decimal("1"), upper=Decimal("1")),
        ),
    )
    candidates = (
        P207PolicyCandidate(
            candidate_id="strict",
            candidate_min_score=Decimal("80"),
            mandatory_min_support=Decimal("0.5"),
            component_weights={"fit": Decimal("1")},
        ),
        P207PolicyCandidate(
            candidate_id="recall-first",
            candidate_min_score=Decimal("40"),
            mandatory_min_support=Decimal("0.5"),
            component_weights={"fit": Decimal("1")},
        ),
    )

    results = search_p207_policy(
        annotations,
        replay_cases,
        candidates,
        min_apply_recall=0.80,
    )

    assert results[0]["candidate"]["candidate_id"] == "recall-first"
    assert results[0]["meets_min_apply_recall"] is True
    assert results[1]["candidate"]["candidate_id"] == "strict"
    assert results[1]["meets_min_apply_recall"] is False


def test_search_p207_policy_keeps_candidates_below_floor_for_frontier_analysis() -> None:
    annotations = (_annotation("apply", AstraDecision.APPLICATION_CANDIDATE),)
    replay_cases = (
        P207ReplayCase(
            pair_id="apply",
            components={"fit": ScoreInterval(lower=50, upper=50)},
            mandatory_support=SupportInterval(lower=Decimal("1"), upper=Decimal("1")),
        ),
    )
    candidates = (
        P207PolicyCandidate(
            candidate_id="strict",
            candidate_min_score=Decimal("80"),
            mandatory_min_support=Decimal("0.5"),
            component_weights={"fit": Decimal("1")},
        ),
    )

    results = search_p207_policy(
        annotations,
        replay_cases,
        candidates,
        min_apply_recall=0.80,
    )

    assert len(results) == 1
    assert results[0]["meets_min_apply_recall"] is False
    assert results[0]["min_apply_recall"] == 0.80
