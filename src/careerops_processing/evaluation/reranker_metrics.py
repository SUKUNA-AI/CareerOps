"""Offline ranking-quality metrics for calibration and replay of P2-05."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def _validate_k(k: int) -> int:
    if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
        raise ValueError("k must be a positive integer")
    return k


def _unique_ranked(ranked_ids: Sequence[str]) -> tuple[str, ...]:
    ranked = tuple(ranked_ids)
    if any(not item for item in ranked):
        raise ValueError("ranked ids must be non-empty strings")
    if len(ranked) != len(set(ranked)):
        raise ValueError("ranked ids must be unique")
    return ranked


def hit_at_k(ranked_ids: Sequence[str], relevant_ids: set[str], k: int) -> float:
    """1 if at least one relevant item is present in top-k, otherwise 0."""

    k = _validate_k(k)
    ranked = _unique_ranked(ranked_ids)
    if not relevant_ids:
        return 0.0
    return float(any(item in relevant_ids for item in ranked[:k]))


def recall_at_k(ranked_ids: Sequence[str], relevant_ids: set[str], k: int) -> float:
    """Fraction of all relevant evidence recovered in top-k."""

    k = _validate_k(k)
    ranked = _unique_ranked(ranked_ids)
    if not relevant_ids:
        return 0.0
    hits = sum(item in relevant_ids for item in ranked[:k])
    return hits / len(relevant_ids)


def precision_at_k(ranked_ids: Sequence[str], relevant_ids: set[str], k: int) -> float:
    """Fraction of returned top-k evidence that is relevant."""

    k = _validate_k(k)
    ranked = _unique_ranked(ranked_ids)
    returned = ranked[:k]
    if not returned:
        return 0.0
    hits = sum(item in relevant_ids for item in returned)
    return hits / len(returned)


def reciprocal_rank(ranked_ids: Sequence[str], relevant_ids: set[str]) -> float:
    """Reciprocal rank of the first relevant evidence item."""

    ranked = _unique_ranked(ranked_ids)
    if not relevant_ids:
        return 0.0
    for rank, item in enumerate(ranked, start=1):
        if item in relevant_ids:
            return 1.0 / rank
    return 0.0


def average_precision_at_k(
    ranked_ids: Sequence[str],
    relevant_ids: set[str],
    k: int,
) -> float:
    """Average precision truncated at k; aggregate mean across queries gives MAP@k."""

    k = _validate_k(k)
    ranked = _unique_ranked(ranked_ids)
    if not relevant_ids:
        return 0.0

    hits = 0
    precision_sum = 0.0
    for rank, item in enumerate(ranked[:k], start=1):
        if item not in relevant_ids:
            continue
        hits += 1
        precision_sum += hits / rank
    return precision_sum / min(len(relevant_ids), k)


def ndcg_at_k(
    ranked_ids: Sequence[str],
    relevance_by_id: Mapping[str, float],
    k: int,
) -> float:
    """NDCG@k for binary or graded requirement-to-evidence relevance labels."""

    k = _validate_k(k)
    ranked = _unique_ranked(ranked_ids)
    if any((not math.isfinite(value) or value < 0) for value in relevance_by_id.values()):
        raise ValueError("relevance grades must be finite non-negative numbers")

    def gain(grade: float, rank: int) -> float:
        return (2.0**grade - 1.0) / math.log2(rank + 1.0)

    dcg = sum(
        gain(float(relevance_by_id.get(item, 0.0)), rank)
        for rank, item in enumerate(ranked[:k], start=1)
    )
    ideal_grades = sorted((float(value) for value in relevance_by_id.values()), reverse=True)[:k]
    idcg = sum(gain(grade, rank) for rank, grade in enumerate(ideal_grades, start=1))
    return dcg / idcg if idcg > 0 else 0.0
