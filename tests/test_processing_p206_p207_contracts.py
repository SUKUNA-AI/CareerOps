from decimal import Decimal

import pytest
from pydantic import ValidationError
from support.processing import HASH_A

from careerops_processing.contracts import (
    MatchDecision,
    MatchDecisionBundle,
    RequirementQualification,
    RequirementQualificationState,
    ScoreBounds,
    ScoringComponent,
    SupportBounds,
)


def _bounds(lower: str, upper: str) -> SupportBounds:
    return SupportBounds(lower=Decimal(lower), upper=Decimal(upper))


def _component(key: str) -> ScoringComponent:
    return ScoringComponent(
        key=key,
        lower=Decimal("50"),
        upper=Decimal("100"),
        weight=Decimal("1"),
    )


def _decision(**overrides: object) -> MatchDecisionBundle:
    payload: dict[str, object] = {
        "input_fingerprint": HASH_A,
        "requirement_qualification_set_sha256": HASH_A,
        "scoring_version": "scoring-v1",
        "calibration_version": "gold-v1",
        "policy_version": "policy-v1",
        "decision": MatchDecision.REVIEW,
        "score": ScoreBounds(lower=Decimal("50"), upper=Decimal("100")),
        "deterministic_score": Decimal("50"),
        "reason_codes": ("match.policy_uncertain",),
    }
    payload.update(overrides)
    return MatchDecisionBundle.model_validate(payload)


def test_qualification_semantic_evidence_must_be_ranked() -> None:
    with pytest.raises(ValidationError, match="must belong to ranked evidence"):
        RequirementQualification(
            requirement_id="req-python",
            state=RequirementQualificationState.MATCHED,
            support=_bounds("1", "1"),
            selection_complete=True,
            ranked_evidence_ids=("ev-ranked",),
            supporting_evidence_ids=("ev-not-ranked",),
            reason_codes=("requirements.direct_support",),
        )


def test_qualification_semantic_evidence_roles_must_be_disjoint() -> None:
    with pytest.raises(ValidationError, match="must be disjoint"):
        RequirementQualification(
            requirement_id="req-python",
            state=RequirementQualificationState.UNKNOWN,
            support=_bounds("0", "1"),
            selection_complete=True,
            ranked_evidence_ids=("ev-conflict",),
            supporting_evidence_ids=("ev-conflict",),
            contradicting_evidence_ids=("ev-conflict",),
            reason_codes=("requirements.source_conflict",),
        )


def test_matched_cannot_carry_contradicting_evidence() -> None:
    with pytest.raises(ValidationError, match="MATCHED cannot contain contradicting"):
        RequirementQualification(
            requirement_id="req-python",
            state=RequirementQualificationState.MATCHED,
            support=_bounds("1", "1"),
            selection_complete=True,
            ranked_evidence_ids=("ev-support", "ev-contradiction"),
            supporting_evidence_ids=("ev-support",),
            contradicting_evidence_ids=("ev-contradiction",),
            reason_codes=("requirements.direct_support",),
        )


def test_match_decision_component_keys_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="component keys must be unique"):
        _decision(components=(_component("technology_fit"), _component("technology_fit")))


def test_match_decision_requirement_state_buckets_must_be_disjoint() -> None:
    with pytest.raises(ValidationError, match="state buckets must be disjoint"):
        _decision(
            critical_conflict_requirement_ids=("req-python",),
            unknown_requirement_ids=("req-python",),
        )


def test_match_decision_reason_codes_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="reason codes must be unique"):
        _decision(reason_codes=("match.policy_uncertain", "match.policy_uncertain"))
