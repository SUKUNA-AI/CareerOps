from __future__ import annotations

from decimal import Decimal

from careerops_processing.calibration import (
    AstraAnnotation,
    AstraDecision,
    P203Prediction,
    P204Prediction,
    P205Prediction,
    P206Prediction,
    P207PolicyCandidate,
    P207Prediction,
    P207ReplayCase,
    PairMetadata,
    SplitName,
    build_grouped_split,
    build_policy_grid,
    evaluate_p203,
    evaluate_p204,
    evaluate_p205,
    evaluate_p206,
    evaluate_p207,
    replay_p207_decision,
)
from careerops_processing.calibration.models import (
    AstraEvidence,
    AstraRequirement,
    AstraRequirementStatus,
    DataSufficiency,
    P203Outcome,
    P204RequirementPrediction,
    RequirementImportanceLabel,
    ScoreInterval,
    SupportInterval,
)


def _annotation(
    pair_id: str,
    decision: AstraDecision,
    *,
    status: AstraRequirementStatus = AstraRequirementStatus.UNKNOWN,
    evidence: tuple[AstraEvidence, ...] = (),
    hard_reject_reasons: tuple[str, ...] = (),
) -> AstraAnnotation:
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
                status=status,
                evidence=evidence,
            ),
        ),
        hard_reject_reasons=hard_reject_reasons,
        reason_codes=(),
        overall_reason="fixture",
    )


def test_grouped_split_keeps_vacancy_groups_disjoint() -> None:
    metadata = tuple(
        PairMetadata(
            pair_id=f"resume-{resume}__hh-{vacancy}",
            source_vacancy_id=str(vacancy),
            resume_key=f"resume-{resume}",
            source_resume_id=f"source-{resume}",
        )
        for vacancy in range(20)
        for resume in (1, 2)
    )
    assignments = build_grouped_split(metadata, seed="fixture")
    vacancy_splits: dict[str, set[SplitName]] = {}
    for item in assignments:
        vacancy_splits.setdefault(item.source_vacancy_id, set()).add(item.split)
    assert all(len(values) == 1 for values in vacancy_splits.values())
    assert {item.split for item in assignments} == set(SplitName)


def test_p203_reports_false_exclusion_for_review() -> None:
    annotations = (
        _annotation("review", AstraDecision.REVIEW),
        _annotation(
            "skip",
            AstraDecision.SKIP,
            hard_reject_reasons=("DIFFERENT_PROFESSION",),
        ),
    )
    predictions = (
        P203Prediction(pair_id="review", outcome=P203Outcome.EXCLUDE_PROVEN),
        P203Prediction(pair_id="skip", outcome=P203Outcome.EXCLUDE_PROVEN),
    )
    report = evaluate_p203(annotations, predictions)
    assert report["false_exclusions"] == 1
    assert report["retention_false_negative_rate"] == 1.0
    assert report["exclusion_precision"] == 0.5


def test_p204_tracks_requirement_and_evidence_recall() -> None:
    annotations = (
        _annotation(
            "pair",
            AstraDecision.APPLICATION_CANDIDATE,
            status=AstraRequirementStatus.SUPPORTED,
            evidence=(AstraEvidence(ref="skill:Python", snippet="Python"),),
        ),
    )
    predictions = (
        P204Prediction(
            pair_id="pair",
            requirements=(
                P204RequirementPrediction(
                    requirement_id="r1",
                    gold_requirement_index=0,
                    evidence_refs=("skill:Python",),
                ),
            ),
        ),
    )
    report = evaluate_p204(annotations, predictions)
    assert report["requirement_recall"] == 1.0
    assert report["significant_requirement_recall"] == 1.0
    assert report["evidence_recall"] == 1.0


def test_p205_uses_gold_evidence_only() -> None:
    annotations = (
        _annotation(
            "pair",
            AstraDecision.APPLICATION_CANDIDATE,
            status=AstraRequirementStatus.SUPPORTED,
            evidence=(
                AstraEvidence(ref="skill:Python", snippet="Python"),
                AstraEvidence(ref="experience:1", snippet="Python pipelines"),
            ),
        ),
    )
    predictions = (
        P205Prediction(
            pair_id="pair",
            gold_requirement_index=0,
            ranked_evidence_refs=("skill:Python", "other", "experience:1"),
        ),
    )
    report = evaluate_p205(annotations, predictions)
    assert report["queries_with_gold_evidence"] == 1
    assert report["recall_at_1"] == 0.5
    assert report["recall_at_3"] == 1.0
    assert report["recall_at_5"] == 1.0
    assert report["mrr"] == 1.0


def test_p206_maps_partial_to_matched_and_unknown_to_unknown() -> None:
    annotations = (
        _annotation("partial", AstraDecision.REVIEW, status=AstraRequirementStatus.PARTIAL),
        _annotation("unknown", AstraDecision.REVIEW, status=AstraRequirementStatus.UNKNOWN),
    )
    predictions = (
        P206Prediction(pair_id="partial", gold_requirement_index=0, state="MATCHED"),
        P206Prediction(pair_id="unknown", gold_requirement_index=0, state="UNKNOWN"),
    )
    report = evaluate_p206(annotations, predictions)
    assert report["coverage"] == 1.0
    assert report["accuracy_on_evaluated"] == 1.0
    assert report["state_recall"] == {
        "MATCHED": 1.0,
        "NOT_EVIDENCED": None,
        "UNKNOWN": 1.0,
        "CONTRADICTED": None,
    }


def test_p207_metrics_and_replay_follow_high_recall_policy() -> None:
    annotations = (
        _annotation("apply", AstraDecision.APPLICATION_CANDIDATE),
        _annotation("review", AstraDecision.REVIEW),
        _annotation("skip", AstraDecision.SKIP, hard_reject_reasons=("DIFFERENT_PROFESSION",)),
    )
    predictions = (
        P207Prediction(pair_id="apply", decision="APPLICATION_CANDIDATE"),
        P207Prediction(pair_id="review", decision="REVIEW"),
        P207Prediction(pair_id="skip", decision="SKIP"),
    )
    report = evaluate_p207(annotations, predictions)
    assert report["application_candidate_recall"] == 1.0
    assert report["skip_precision"] == 1.0
    assert report["review_rate"] == 1 / 3

    candidate = P207PolicyCandidate(
        candidate_id="fixture",
        candidate_min_score=Decimal("60"),
        mandatory_min_support=Decimal("0.5"),
        component_weights={"role_fit": Decimal("1")},
    )
    assert replay_p207_decision(
        P207ReplayCase(
            pair_id="apply",
            components={"role_fit": ScoreInterval(lower=80, upper=90)},
            mandatory_support=SupportInterval(lower=Decimal("0.8"), upper=Decimal("0.9")),
        ),
        candidate,
    ) is AstraDecision.APPLICATION_CANDIDATE
    assert replay_p207_decision(
        P207ReplayCase(
            pair_id="review",
            components={"role_fit": ScoreInterval(lower=50, upper=80)},
            mandatory_support=SupportInterval(lower=Decimal("0.4"), upper=Decimal("0.8")),
        ),
        candidate,
    ) is AstraDecision.REVIEW
    assert replay_p207_decision(
        P207ReplayCase(
            pair_id="skip",
            components={"role_fit": ScoreInterval(lower=90, upper=100)},
            mandatory_support=SupportInterval(lower=Decimal("1"), upper=Decimal("1")),
            critical_conflict=True,
        ),
        candidate,
    ) is AstraDecision.SKIP


def test_policy_grid_search_space_is_explicit_and_bounded() -> None:
    candidates = build_policy_grid(
        candidate_min_scores=(Decimal("50"), Decimal("60")),
        mandatory_min_supports=(Decimal("0.5"),),
        component_weight_options={
            "role_fit": (Decimal("1"), Decimal("2")),
            "technology_fit": (Decimal("1"),),
        },
    )
    assert len(candidates) == 4
    assert {item.candidate_min_score for item in candidates} == {Decimal("50"), Decimal("60")}
