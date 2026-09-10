from __future__ import annotations

from datetime import date
from decimal import Decimal

from support.processing import (
    candidate_set,
    evidence,
    evidence_set,
    prohibited_requirement,
    qualify,
    requirement,
    requirement_set,
    score,
)

from careerops_processing.contracts import (
    EvidenceActorScope,
    MatchDecision,
    RequirementGroup,
    RequirementGroupOperator,
    RequirementImportance,
    RequirementQualificationState,
    RequirementThreshold,
    RequirementThresholdMetric,
    SemanticPolarity,
    SemanticTimeSpan,
)


def test_low_jina_score_does_not_block_direct_match() -> None:
    requirements = requirement_set(requirement("req-python", "Python"))
    resume_evidence = evidence_set(evidence("ev-python", "Python"))
    candidates = candidate_set(requirements, resume_evidence, scores={"ev-python": 0.01})

    result = qualify(requirements, resume_evidence, candidates)

    assert result.evaluations[0].state is RequirementQualificationState.MATCHED
    assert result.evaluations[0].support.lower == Decimal("1")


def test_empty_evidence_is_not_evidenced_not_contradicted() -> None:
    requirements = requirement_set(requirement("req-python", "Python"))

    result = qualify(requirements, evidence_set())

    assert result.evaluations[0].state is RequirementQualificationState.NOT_EVIDENCED
    assert result.evaluations[0].support.lower == Decimal("0")
    assert result.evaluations[0].support.upper == Decimal("1")


def test_team_mention_remains_unknown() -> None:
    requirements = requirement_set(requirement("req-k8s", "Kubernetes"))
    resume_evidence = evidence_set(
        evidence("ev-k8s-team", "Kubernetes", actor_scope=EvidenceActorScope.TEAM)
    )

    result = qualify(requirements, resume_evidence)

    assert result.evaluations[0].state is RequirementQualificationState.UNKNOWN


def test_explicit_negative_self_evidence_is_contradiction() -> None:
    requirements = requirement_set(requirement("req-k8s", "Kubernetes"))
    resume_evidence = evidence_set(
        evidence("ev-k8s-negative", "Kubernetes", polarity=SemanticPolarity.NEGATIVE)
    )

    result = qualify(requirements, resume_evidence)

    assert result.evaluations[0].state is RequirementQualificationState.CONTRADICTED


def test_conflicting_positive_and_negative_sources_are_unknown() -> None:
    requirements = requirement_set(requirement("req-k8s", "Kubernetes"))
    resume_evidence = evidence_set(
        evidence("ev-positive", "Kubernetes"),
        evidence("ev-negative", "Kubernetes", polarity=SemanticPolarity.NEGATIVE),
    )

    result = qualify(requirements, resume_evidence)

    assert result.evaluations[0].state is RequirementQualificationState.UNKNOWN
    assert result.evaluations[0].support.lower == Decimal("0")
    assert result.evaluations[0].support.upper == Decimal("1")


def test_incomplete_pre_jina_pool_cannot_prove_not_evidenced() -> None:
    requirements = requirement_set(requirement("req-rust", "Rust"))
    resume_evidence = evidence_set(evidence("ev-python", "Python"), evidence("ev-sql", "SQL"))
    candidates = candidate_set(
        requirements,
        resume_evidence,
        pool_ids_by_requirement={"req-rust": ("ev-python",)},
    )

    result = qualify(requirements, resume_evidence, candidates)

    assert result.evaluations[0].state is RequirementQualificationState.UNKNOWN
    assert result.evaluations[0].selection_complete is False


def test_truncated_jina_top_k_cannot_use_tail_or_prove_not_evidenced() -> None:
    requirements = requirement_set(requirement("req-python", "Python"))
    resume_evidence = evidence_set(
        evidence("ev-python", "Python"),
        evidence("ev-sql", "SQL"),
    )
    candidates = candidate_set(
        requirements,
        resume_evidence,
        selected_ids_by_requirement={"req-python": ("ev-sql",)},
    )

    result = qualify(requirements, resume_evidence, candidates)
    evaluation = result.evaluations[0]

    assert evaluation.state is RequirementQualificationState.UNKNOWN
    assert evaluation.selection_complete is False
    assert evaluation.ranked_evidence_ids == ("ev-sql",)
    assert evaluation.supporting_evidence_ids == ()


def test_exhaustive_selection_can_prove_not_evidenced() -> None:
    requirements = requirement_set(requirement("req-python", "Python"))
    resume_evidence = evidence_set(evidence("ev-sql", "SQL"))

    result = qualify(requirements, resume_evidence)

    assert result.evaluations[0].state is RequirementQualificationState.NOT_EVIDENCED
    assert result.evaluations[0].selection_complete is True


def test_experience_threshold_uses_evidenced_duration_without_contradiction() -> None:
    requirements = requirement_set(
        requirement(
            "req-python-years",
            "Python",
            threshold=RequirementThreshold(
                metric=RequirementThresholdMetric.EXPERIENCE_YEARS,
                minimum=Decimal("3"),
            ),
        )
    )
    resume_evidence = evidence_set(
        evidence(
            "ev-python",
            "Python",
            time_span=SemanticTimeSpan(
                start_date=date(2024, 1, 1),
                end_date=date(2025, 1, 1),
                currently_active=False,
            ),
        )
    )

    result = qualify(requirements, resume_evidence)

    assert result.evaluations[0].state is RequirementQualificationState.NOT_EVIDENCED


def test_truncated_jina_selection_cannot_prove_insufficient_experience() -> None:
    requirements = requirement_set(
        requirement(
            "req-python-years",
            "Python",
            threshold=RequirementThreshold(
                metric=RequirementThresholdMetric.EXPERIENCE_YEARS,
                minimum=Decimal("3"),
            ),
        )
    )
    resume_evidence = evidence_set(
        evidence(
            "ev-selected",
            "Python",
            time_span=SemanticTimeSpan(
                start_date=date(2024, 1, 1),
                end_date=date(2025, 1, 1),
                currently_active=False,
            ),
        ),
        evidence(
            "ev-tail",
            "Python",
            time_span=SemanticTimeSpan(
                start_date=date(2021, 1, 1),
                end_date=date(2024, 1, 1),
                currently_active=False,
            ),
        ),
    )
    candidates = candidate_set(
        requirements,
        resume_evidence,
        selected_ids_by_requirement={"req-python-years": ("ev-selected",)},
    )

    result = qualify(requirements, resume_evidence, candidates)
    evaluation = result.evaluations[0]

    assert evaluation.state is RequirementQualificationState.UNKNOWN
    assert evaluation.selection_complete is False
    assert evaluation.supporting_evidence_ids == ("ev-selected",)


def test_any_group_uses_max_support_and_is_one_scoring_unit() -> None:
    python = requirement("req-python", "Python")
    scala = requirement("req-scala", "Scala")
    sql = requirement("req-sql", "SQL")
    groups = (
        RequirementGroup(
            group_id="root",
            operator=RequirementGroupOperator.ALL,
            requirement_ids=("req-sql",),
            child_group_ids=("language",),
        ),
        RequirementGroup(
            group_id="language",
            operator=RequirementGroupOperator.ANY,
            requirement_ids=("req-python", "req-scala"),
        ),
    )
    requirements = requirement_set(python, scala, sql, groups=groups)
    resume_evidence = evidence_set(evidence("ev-python", "Python"), evidence("ev-sql", "SQL"))
    qualification = qualify(requirements, resume_evidence)

    language = next(item for item in qualification.groups if item.group_id == "language")
    assert language.support.lower == Decimal("1")

    decision = score(requirements, qualification)
    mandatory = next(item for item in decision.components if item.key == "mandatory_coverage")
    assert mandatory.lower == Decimal("100")
    assert decision.decision is MatchDecision.APPLICATION_CANDIDATE


def test_required_mandatory_contradiction_uses_policy_not_critical_gate() -> None:
    requirements = requirement_set(
        requirement("req-k8s", "Kubernetes"),
        requirement("req-python", "Python", importance=RequirementImportance.OPTIONAL),
    )
    resume_evidence = evidence_set(
        evidence("ev-k8s-negative", "Kubernetes", polarity=SemanticPolarity.NEGATIVE),
        evidence("ev-python", "Python"),
    )
    qualification = qualify(requirements, resume_evidence)

    decision = score(requirements, qualification)

    assert decision.decision is MatchDecision.SKIP
    assert decision.deterministic_score == Decimal("0")
    assert decision.critical_conflict_requirement_ids == ()
    assert decision.reason_codes == ("match.policy_not_satisfied",)


def test_required_mandatory_contradiction_stays_review_before_calibration() -> None:
    requirements = requirement_set(requirement("req-k8s", "Kubernetes"))
    resume_evidence = evidence_set(
        evidence("ev-k8s-negative", "Kubernetes", polarity=SemanticPolarity.NEGATIVE)
    )
    qualification = qualify(requirements, resume_evidence)

    decision = score(requirements, qualification, calibrated=False)

    assert decision.decision is MatchDecision.REVIEW
    assert decision.critical_conflict_requirement_ids == ()
    assert decision.reason_codes == ("match.calibration_unset",)


def test_prohibited_contradiction_is_non_compensable_hard_skip() -> None:
    requirements = requirement_set(
        prohibited_requirement("req-windows", "Windows"),
        requirement("req-python", "Python", importance=RequirementImportance.OPTIONAL),
    )
    resume_evidence = evidence_set(
        evidence("ev-windows", "Windows", polarity=SemanticPolarity.POSITIVE),
        evidence("ev-python", "Python"),
    )
    qualification = qualify(requirements, resume_evidence)

    decision = score(requirements, qualification, calibrated=False)

    assert decision.decision is MatchDecision.SKIP
    assert decision.deterministic_score == Decimal("0")
    assert decision.critical_conflict_requirement_ids == ("req-windows",)
    assert decision.reason_codes == ("requirements.critical_contradiction",)


def test_unset_calibration_never_auto_publishes_application_candidate() -> None:
    requirements = requirement_set(requirement("req-python", "Python"))
    resume_evidence = evidence_set(evidence("ev-python", "Python"))
    qualification = qualify(requirements, resume_evidence)

    decision = score(requirements, qualification, calibrated=False)

    assert decision.decision is MatchDecision.REVIEW
    assert decision.reason_codes == ("match.calibration_unset",)


def test_calibrated_uncertainty_returns_review() -> None:
    requirements = requirement_set(
        requirement("req-python", "Python", importance=RequirementImportance.PREFERRED)
    )
    qualification = qualify(requirements, evidence_set())

    decision = score(
        requirements,
        qualification,
        mandatory_min_support="0",
        weights={"preferred_coverage": "1"},
    )

    assert decision.score.lower == Decimal("0")
    assert decision.score.upper == Decimal("100")
    assert decision.decision is MatchDecision.REVIEW
