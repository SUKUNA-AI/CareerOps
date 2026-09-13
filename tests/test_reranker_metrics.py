from __future__ import annotations

from careerops_reranker.metrics import RerankerMetrics


def test_reranker_metrics_exposes_requested_prometheus_series() -> None:
    metrics = RerankerMetrics()
    metrics.observe_request(
        latency_seconds=0.123,
        documents=18,
        selected=5,
        total_tokens=842,
        error_class=None,
    )
    metrics.observe_request(
        latency_seconds=0.010,
        documents=4,
        selected=0,
        total_tokens=None,
        error_class="runtime_identity_mismatch",
    )

    rendered = metrics.render().decode("utf-8")

    assert "reranker_requests_total 2" in rendered
    assert 'reranker_errors_total{error_class="runtime_identity_mismatch"} 1' in rendered
    assert "reranker_request_duration_seconds_count 2" in rendered
    assert "reranker_tokens_total 842" in rendered
    assert "reranker_documents_per_request_count 2" in rendered
    assert "reranker_selected_per_request_count 2" in rendered
