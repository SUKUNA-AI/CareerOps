"""Stage-specific calibration metrics for P2-03 through P2-07."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable

from careerops_processing.evaluation.reranker_metrics import (
    average_precision_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

from .models import (
    AstraAnnotation,
    AstraDecision,
    AstraRequirementStatus,
    P203Outcome,
    P203Prediction,
    P204Prediction,
    P205Prediction,
    P206Prediction,
    P207Prediction,
    QualificationStateLabel,
    RequirementImportanceLabel,
)


def _unique_by_pair[T](values: tuple[T, ...], pair_id: Callable[[T], str]) -> dict[str, T]:
    result: dict[str, T] = {}
    for item in values:
        key = pair_id(item)
        if key in result:
            raise ValueError(f"duplicate prediction for pair_id={key}")
        result[key] = item
    return result


def _safe_ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def evaluate_p203(
    annotations: tuple[AstraAnnotation, ...],
    predictions: tuple[P203Prediction, ...],
) -> dict[str, object]:
    prediction_by_pair = _unique_by_pair(predictions, lambda item: item.pair_id)
    annotation_ids = {item.pair_id for item in annotations}
    unknown_predictions = sorted(set(prediction_by_pair) - annotation_ids)
    if unknown_predictions:
        raise ValueError(f"P2-03 predictions contain unknown pair ids: {unknown_predictions[:3]}")

    expected_keep = 0
    expected_exclude = 0
    predicted_keep = 0
    predicted_exclude = 0
    false_exclusions = 0
    missed_exclusions = 0
    evaluated = 0

    for annotation in annotations:
        prediction = prediction_by_pair.get(annotation.pair_id)
        if prediction is None:
            continue
        evaluated += 1
        gold_keep = not (
            annotation.decision is AstraDecision.SKIP and bool(annotation.hard_reject_reasons)
        )
        actual_keep = prediction.outcome is P203Outcome.KEEP
        expected_keep += int(gold_keep)
        expected_exclude += int(not gold_keep)
        predicted_keep += int(actual_keep)
        predicted_exclude += int(not actual_keep)
        false_exclusions += int(gold_keep and not actual_keep)
        missed_exclusions += int(not gold_keep and actual_keep)

    correct_exclusions = expected_exclude - missed_exclusions
    return {
        "stage": "P2-03",
        "evaluated_pairs": evaluated,
        "missing_predictions": len(annotations) - evaluated,
        "expected_keep": expected_keep,
        "expected_exclude_proven": expected_exclude,
        "predicted_keep": predicted_keep,
        "predicted_exclude_proven": predicted_exclude,
        "false_exclusions": false_exclusions,
        "missed_exclusions": missed_exclusions,
        "retention_false_negative_rate": _safe_ratio(false_exclusions, expected_keep),
        "retention_recall": 1.0 - _safe_ratio(false_exclusions, expected_keep),
        "exclusion_precision": _safe_ratio(correct_exclusions, predicted_exclude, empty=1.0),
        "exclusion_recall": _safe_ratio(correct_exclusions, expected_exclude, empty=1.0),
    }


def evaluate_p204(
    annotations: tuple[AstraAnnotation, ...],
    predictions: tuple[P204Prediction, ...],
) -> dict[str, object]:
    prediction_by_pair = _unique_by_pair(predictions, lambda item: item.pair_id)
    extracted_gold: set[tuple[str, int]] = set()
    duplicate_alignments = 0
    false_positive_requirements = 0
    predicted_requirement_count = 0
    gold_requirement_count = 0
    gold_significant_count = 0
    extracted_significant = 0
    evidence_gold_total = 0
    evidence_predicted_total = 0
    evidence_hits = 0

    for annotation in annotations:
        gold_requirement_count += len(annotation.requirements)
        significant_indexes = {
            index
            for index, requirement in enumerate(annotation.requirements)
            if requirement.importance
            in {RequirementImportanceLabel.MANDATORY, RequirementImportanceLabel.PREFERRED}
        }
        gold_significant_count += len(significant_indexes)
        prediction = prediction_by_pair.get(annotation.pair_id)
        if prediction is None:
            continue

        aligned_refs: dict[int, set[str]] = defaultdict(set)
        for predicted in prediction.requirements:
            predicted_requirement_count += 1
            if predicted.gold_requirement_index is None:
                false_positive_requirements += 1
                continue
            index = predicted.gold_requirement_index
            if index >= len(annotation.requirements):
                raise ValueError(
                    f"P2-04 gold_requirement_index out of range for {annotation.pair_id}: {index}"
                )
            key = (annotation.pair_id, index)
            if key in extracted_gold:
                duplicate_alignments += 1
            extracted_gold.add(key)
            aligned_refs[index].update(predicted.evidence_refs)

        extracted_significant += len(significant_indexes & set(aligned_refs))
        for index, requirement in enumerate(annotation.requirements):
            gold_refs = {item.ref for item in requirement.evidence}
            if not gold_refs:
                continue
            predicted_refs = aligned_refs.get(index, set())
            evidence_gold_total += len(gold_refs)
            evidence_predicted_total += len(predicted_refs)
            evidence_hits += len(gold_refs & predicted_refs)

    aligned_prediction_count = predicted_requirement_count - false_positive_requirements
    return {
        "stage": "P2-04",
        "gold_requirement_count": gold_requirement_count,
        "predicted_requirement_count": predicted_requirement_count,
        "aligned_gold_requirements": len(extracted_gold),
        "false_positive_requirements": false_positive_requirements,
        "duplicate_alignments": duplicate_alignments,
        "requirement_recall": _safe_ratio(len(extracted_gold), gold_requirement_count),
        "requirement_precision": _safe_ratio(
            aligned_prediction_count, predicted_requirement_count, empty=1.0
        ),
        "significant_requirement_recall": _safe_ratio(
            extracted_significant, gold_significant_count, empty=1.0
        ),
        "evidence_recall": _safe_ratio(evidence_hits, evidence_gold_total, empty=1.0),
        "evidence_precision": _safe_ratio(
            evidence_hits, evidence_predicted_total, empty=1.0
        ),
        "gold_evidence_refs": evidence_gold_total,
        "predicted_evidence_refs": evidence_predicted_total,
    }


def evaluate_p205(
    annotations: tuple[AstraAnnotation, ...],
    predictions: tuple[P205Prediction, ...],
) -> dict[str, object]:
    annotation_by_pair = {item.pair_id: item for item in annotations}
    seen: set[tuple[str, int]] = set()
    recall_1: list[float] = []
    recall_3: list[float] = []
    recall_5: list[float] = []
    precision_5: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcg_5: list[float] = []
    ap_5: list[float] = []

    for prediction in predictions:
        key = (prediction.pair_id, prediction.gold_requirement_index)
        if key in seen:
            raise ValueError(f"duplicate P2-05 prediction for {key}")
        seen.add(key)
        annotation = annotation_by_pair.get(prediction.pair_id)
        if annotation is None:
            raise ValueError(f"P2-05 prediction contains unknown pair_id={prediction.pair_id}")
        index = prediction.gold_requirement_index
        if index >= len(annotation.requirements):
            raise ValueError(f"P2-05 gold_requirement_index out of range for {key}")
        relevant = {item.ref for item in annotation.requirements[index].evidence}
        if not relevant:
            continue
        ranked = prediction.ranked_evidence_refs
        recall_1.append(recall_at_k(ranked, relevant, 1))
        recall_3.append(recall_at_k(ranked, relevant, 3))
        recall_5.append(recall_at_k(ranked, relevant, 5))
        precision_5.append(precision_at_k(ranked, relevant, 5))
        reciprocal_ranks.append(reciprocal_rank(ranked, relevant))
        grades = {item: 1.0 for item in relevant}
        ndcg_5.append(ndcg_at_k(ranked, grades, 5))
        ap_5.append(average_precision_at_k(ranked, relevant, 5))

    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return {
        "stage": "P2-05",
        "queries_with_gold_evidence": len(recall_5),
        "recall_at_1": mean(recall_1),
        "recall_at_3": mean(recall_3),
        "recall_at_5": mean(recall_5),
        "precision_at_5": mean(precision_5),
        "mrr": mean(reciprocal_ranks),
        "ndcg_at_5": mean(ndcg_5),
        "map_at_5": mean(ap_5),
    }


def _gold_qualification_state(status: AstraRequirementStatus) -> QualificationStateLabel:
    if status in {AstraRequirementStatus.SUPPORTED, AstraRequirementStatus.PARTIAL}:
        return QualificationStateLabel.MATCHED
    if status is AstraRequirementStatus.UNSUPPORTED:
        return QualificationStateLabel.NOT_EVIDENCED
    if status is AstraRequirementStatus.CONTRADICTED:
        return QualificationStateLabel.CONTRADICTED
    return QualificationStateLabel.UNKNOWN


def evaluate_p206(
    annotations: tuple[AstraAnnotation, ...],
    predictions: tuple[P206Prediction, ...],
) -> dict[str, object]:
    annotation_by_pair = {item.pair_id: item for item in annotations}
    seen: set[tuple[str, int]] = set()
    matrix: dict[str, Counter[str]] = defaultdict(Counter)
    gold_counts: Counter[str] = Counter()
    correct = 0

    for annotation in annotations:
        for requirement in annotation.requirements:
            gold_counts[_gold_qualification_state(requirement.status).value] += 1

    for prediction in predictions:
        key = (prediction.pair_id, prediction.gold_requirement_index)
        if key in seen:
            raise ValueError(f"duplicate P2-06 prediction for {key}")
        seen.add(key)
        selected_annotation = annotation_by_pair.get(prediction.pair_id)
        if selected_annotation is None:
            raise ValueError(f"P2-06 prediction contains unknown pair_id={prediction.pair_id}")
        if prediction.gold_requirement_index >= len(selected_annotation.requirements):
            raise ValueError(f"P2-06 gold_requirement_index out of range for {key}")
        gold = _gold_qualification_state(
            selected_annotation.requirements[prediction.gold_requirement_index].status
        )
        matrix[gold.value][prediction.state.value] += 1
        correct += int(gold is prediction.state)

    state_recall: dict[str, float | None] = {}
    for state in QualificationStateLabel:
        gold_count = gold_counts[state.value]
        if gold_count == 0:
            state_recall[state.value] = None
        else:
            state_recall[state.value] = _safe_ratio(matrix[state.value][state.value], gold_count)

    total_gold = sum(gold_counts.values())
    return {
        "stage": "P2-06",
        "gold_requirement_count": total_gold,
        "evaluated_requirements": len(predictions),
        "coverage": _safe_ratio(len(predictions), total_gold),
        "accuracy_on_evaluated": _safe_ratio(correct, len(predictions)),
        "gold_state_counts": dict(sorted(gold_counts.items())),
        "state_recall": state_recall,
        "confusion_matrix": {
            state: dict(sorted(values.items())) for state, values in sorted(matrix.items())
        },
    }


def evaluate_p207(
    annotations: tuple[AstraAnnotation, ...],
    predictions: tuple[P207Prediction, ...],
) -> dict[str, object]:
    prediction_by_pair = _unique_by_pair(predictions, lambda item: item.pair_id)
    matrix: dict[str, Counter[str]] = defaultdict(Counter)
    gold_counts: Counter[str] = Counter()
    predicted_counts: Counter[str] = Counter()
    evaluated = 0
    correct = 0

    for annotation in annotations:
        gold_counts[annotation.decision.value] += 1
        prediction = prediction_by_pair.get(annotation.pair_id)
        if prediction is None:
            continue
        evaluated += 1
        predicted_counts[prediction.decision.value] += 1
        matrix[annotation.decision.value][prediction.decision.value] += 1
        correct += int(annotation.decision is prediction.decision)

    candidate_key = AstraDecision.APPLICATION_CANDIDATE.value
    skip_key = AstraDecision.SKIP.value
    review_key = AstraDecision.REVIEW.value
    candidate_tp = matrix[candidate_key][candidate_key]
    skip_tp = matrix[skip_key][skip_key]

    return {
        "stage": "P2-07",
        "evaluated_pairs": evaluated,
        "missing_predictions": len(annotations) - evaluated,
        "accuracy": _safe_ratio(correct, evaluated),
        "application_candidate_recall": _safe_ratio(candidate_tp, gold_counts[candidate_key]),
        "application_candidate_precision": _safe_ratio(
            candidate_tp, predicted_counts[candidate_key], empty=1.0
        ),
        "skip_recall": _safe_ratio(skip_tp, gold_counts[skip_key]),
        "skip_precision": _safe_ratio(skip_tp, predicted_counts[skip_key], empty=1.0),
        "review_rate": _safe_ratio(predicted_counts[review_key], evaluated),
        "gold_decision_counts": dict(sorted(gold_counts.items())),
        "predicted_decision_counts": dict(sorted(predicted_counts.items())),
        "confusion_matrix": {
            decision: dict(sorted(values.items()))
            for decision, values in sorted(matrix.items())
        },
    }
