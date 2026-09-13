"""Replay and search calibrated P2-07 thresholds and component weights."""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from decimal import Decimal

from .evaluators import evaluate_p207
from .models import (
    AstraAnnotation,
    AstraDecision,
    P207PolicyCandidate,
    P207Prediction,
    P207ReplayCase,
    ScoreInterval,
)

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def _weighted_score(
    case: P207ReplayCase,
    candidate: P207PolicyCandidate,
) -> ScoreInterval:
    weight_sum = sum(candidate.component_weights.values(), _ZERO)
    lower = _ZERO
    upper = _ZERO
    for key, weight in candidate.component_weights.items():
        if weight <= 0:
            continue
        bounds = case.components.get(key, ScoreInterval(lower=_ZERO, upper=_HUNDRED))
        lower += bounds.lower * weight
        upper += bounds.upper * weight
    return ScoreInterval(lower=lower / weight_sum, upper=upper / weight_sum)


def replay_p207_decision(
    case: P207ReplayCase,
    candidate: P207PolicyCandidate,
) -> AstraDecision:
    """Mirror production P2-07 score/mandatory bounds and critical-conflict gates."""

    if case.critical_conflict:
        return AstraDecision.SKIP
    score = _weighted_score(case, candidate)
    if (
        case.mandatory_support.upper < candidate.mandatory_min_support
        or score.upper < candidate.candidate_min_score
    ):
        return AstraDecision.SKIP
    if (
        case.mandatory_support.lower >= candidate.mandatory_min_support
        and score.lower >= candidate.candidate_min_score
    ):
        return AstraDecision.APPLICATION_CANDIDATE
    return AstraDecision.REVIEW


def build_policy_grid(
    *,
    candidate_min_scores: Sequence[Decimal],
    mandatory_min_supports: Sequence[Decimal],
    component_weight_options: Mapping[str, Sequence[Decimal]],
    max_candidates: int = 100_000,
) -> tuple[P207PolicyCandidate, ...]:
    if not candidate_min_scores or not mandatory_min_supports or not component_weight_options:
        raise ValueError("policy search space must not be empty")
    keys = tuple(sorted(component_weight_options))
    options = tuple(tuple(component_weight_options[key]) for key in keys)
    if any(not values for values in options):
        raise ValueError("each component must contain at least one candidate weight")

    estimated = len(candidate_min_scores) * len(mandatory_min_supports)
    for values in options:
        estimated *= len(values)
    if estimated > max_candidates:
        raise ValueError(
            f"policy grid would create {estimated} candidates; max_candidates={max_candidates}"
        )

    values: list[P207PolicyCandidate] = []
    candidate_number = 0
    for score, mandatory, weights in itertools.product(
        candidate_min_scores,
        mandatory_min_supports,
        itertools.product(*options),
    ):
        weight_map = dict(zip(keys, weights, strict=True))
        if sum(weight_map.values(), _ZERO) <= 0:
            continue
        candidate_number += 1
        values.append(
            P207PolicyCandidate(
                candidate_id=f"candidate-{candidate_number:06d}",
                candidate_min_score=score,
                mandatory_min_support=mandatory,
                component_weights=weight_map,
            )
        )
    if not values:
        raise ValueError("policy grid contains no positive-weight candidates")
    return tuple(values)


def _high_recall_cost(gold: AstraDecision, predicted: AstraDecision) -> Decimal:
    if gold is predicted:
        return _ZERO
    costs: dict[tuple[AstraDecision, AstraDecision], Decimal] = {
        (AstraDecision.APPLICATION_CANDIDATE, AstraDecision.REVIEW): Decimal("2"),
        (AstraDecision.APPLICATION_CANDIDATE, AstraDecision.SKIP): Decimal("10"),
        (AstraDecision.REVIEW, AstraDecision.APPLICATION_CANDIDATE): Decimal("1"),
        (AstraDecision.REVIEW, AstraDecision.SKIP): Decimal("3"),
        (AstraDecision.SKIP, AstraDecision.REVIEW): Decimal("0.5"),
        (AstraDecision.SKIP, AstraDecision.APPLICATION_CANDIDATE): Decimal("2"),
    }
    return costs[(gold, predicted)]


def search_p207_policy(
    annotations: tuple[AstraAnnotation, ...],
    replay_cases: tuple[P207ReplayCase, ...],
    candidates: tuple[P207PolicyCandidate, ...],
    *,
    allowed_pair_ids: set[str] | None = None,
) -> tuple[dict[str, object], ...]:
    annotation_by_pair = {item.pair_id: item for item in annotations}
    case_by_pair = {item.pair_id: item for item in replay_cases}
    if len(case_by_pair) != len(replay_cases):
        raise ValueError("P2-07 replay pair_id values must be unique")

    pair_ids = set(annotation_by_pair) & set(case_by_pair)
    if allowed_pair_ids is not None:
        pair_ids &= allowed_pair_ids
    if not pair_ids:
        raise ValueError("P2-07 policy search has no overlapping pairs")

    selected_annotations = tuple(annotation_by_pair[pair_id] for pair_id in sorted(pair_ids))
    ranked: list[dict[str, object]] = []
    for candidate in candidates:
        predictions = tuple(
            P207Prediction(
                pair_id=pair_id,
                decision=replay_p207_decision(case_by_pair[pair_id], candidate),
            )
            for pair_id in sorted(pair_ids)
        )
        metrics = evaluate_p207(selected_annotations, predictions)
        total_cost = sum(
            (
                _high_recall_cost(
                    annotation_by_pair[prediction.pair_id].decision,
                    prediction.decision,
                )
                for prediction in predictions
            ),
            _ZERO,
        )
        ranked.append(
            {
                "candidate": candidate.model_dump(mode="json"),
                "weighted_cost": str(total_cost),
                "metrics": metrics,
            }
        )

    def sort_key(item: dict[str, object]) -> tuple[Decimal, float, float]:
        metrics = item["metrics"]
        if not isinstance(metrics, dict):
            raise TypeError("invalid policy-search metrics")
        apply_recall = float(metrics["application_candidate_recall"])
        review_rate = float(metrics["review_rate"])
        return Decimal(str(item["weighted_cost"])), -apply_recall, review_rate

    return tuple(sorted(ranked, key=sort_key))
