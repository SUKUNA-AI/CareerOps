from __future__ import annotations

import pytest

from careerops_processing.evaluation import (
    average_precision_at_k,
    hit_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)


def test_binary_ranking_metrics_for_multiple_relevant_evidence() -> None:
    ranked = ("a", "b", "c", "d")
    relevant = {"b", "c"}

    assert hit_at_k(ranked, relevant, 1) == 0.0
    assert hit_at_k(ranked, relevant, 2) == 1.0
    assert recall_at_k(ranked, relevant, 1) == 0.0
    assert recall_at_k(ranked, relevant, 2) == 0.5
    assert recall_at_k(ranked, relevant, 3) == 1.0
    assert precision_at_k(ranked, relevant, 2) == 0.5
    assert reciprocal_rank(ranked, relevant) == 0.5
    assert average_precision_at_k(ranked, relevant, 3) == pytest.approx((0.5 + 2 / 3) / 2)


def test_ndcg_supports_graded_relevance() -> None:
    ranked = ("weak", "best", "good")
    relevance = {"best": 3.0, "good": 2.0, "weak": 1.0}

    value = ndcg_at_k(ranked, relevance, 3)

    assert 0.0 < value < 1.0
    assert ndcg_at_k(("best", "good", "weak"), relevance, 3) == pytest.approx(1.0)


def test_metrics_reject_duplicate_ranked_ids() -> None:
    with pytest.raises(ValueError, match="unique"):
        recall_at_k(("a", "a"), {"a"}, 2)
