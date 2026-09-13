"""Thread-safe Prometheus exposition for the standalone reranker runtime."""

from __future__ import annotations

import math
import threading
from collections import Counter
from dataclasses import dataclass, field


_DURATION_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)
_DOCUMENT_BUCKETS = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0)
_SELECTED_BUCKETS = (1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0, 100.0)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


@dataclass(slots=True)
class _Histogram:
    buckets: tuple[float, ...]
    counts: list[int] = field(init=False)
    count: int = 0
    total: float = 0.0

    def __post_init__(self) -> None:
        self.counts = [0 for _ in self.buckets]

    def observe(self, value: float) -> None:
        if not math.isfinite(value) or value < 0:
            raise ValueError("histogram observation must be a finite non-negative number")
        self.count += 1
        self.total += value
        for index, upper_bound in enumerate(self.buckets):
            if value <= upper_bound:
                self.counts[index] += 1

    def render(self, name: str) -> list[str]:
        lines: list[str] = []
        for upper_bound, count in zip(self.buckets, self.counts, strict=True):
            lines.append(f'{name}_bucket{{le="{upper_bound:g}"}} {count}')
        lines.append(f'{name}_bucket{{le="+Inf"}} {self.count}')
        lines.append(f"{name}_sum {self.total:.12g}")
        lines.append(f"{name}_count {self.count}")
        return lines


class RerankerMetrics:
    """Small dependency-free Prometheus registry for one reranker process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests_total = 0
        self._errors_total: Counter[str] = Counter()
        self._tokens_total = 0
        self._duration = _Histogram(_DURATION_BUCKETS)
        self._documents = _Histogram(_DOCUMENT_BUCKETS)
        self._selected = _Histogram(_SELECTED_BUCKETS)

    def observe_request(
        self,
        *,
        latency_seconds: float,
        documents: int | None,
        selected: int | None,
        total_tokens: int | None,
        error_class: str | None,
    ) -> None:
        if documents is not None and documents < 0:
            raise ValueError("documents must be non-negative")
        if selected is not None and selected < 0:
            raise ValueError("selected must be non-negative")
        if total_tokens is not None and total_tokens < 0:
            raise ValueError("total_tokens must be non-negative")
        if error_class is not None and not error_class.strip():
            raise ValueError("error_class must not be blank")

        with self._lock:
            self._requests_total += 1
            self._duration.observe(latency_seconds)
            if documents is not None:
                self._documents.observe(float(documents))
            if selected is not None:
                self._selected.observe(float(selected))
            if total_tokens is not None:
                self._tokens_total += total_tokens
            if error_class is not None:
                self._errors_total[error_class] += 1

    def render(self) -> bytes:
        with self._lock:
            lines = [
                "# HELP reranker_requests_total Total HTTP rerank requests handled.",
                "# TYPE reranker_requests_total counter",
                f"reranker_requests_total {self._requests_total}",
                "# HELP reranker_errors_total Rerank request errors by stable error class.",
                "# TYPE reranker_errors_total counter",
            ]
            if not self._errors_total:
                lines.append('reranker_errors_total{error_class="none"} 0')
            else:
                for error_class, count in sorted(self._errors_total.items()):
                    lines.append(
                        "reranker_errors_total"
                        f'{{error_class="{_escape_label(error_class)}"}} {count}'
                    )

            lines.extend(
                [
                    "# HELP reranker_request_duration_seconds End-to-end rerank HTTP latency.",
                    "# TYPE reranker_request_duration_seconds histogram",
                    *self._duration.render("reranker_request_duration_seconds"),
                    "# HELP reranker_tokens_total Exact prompt tokens processed for successful requests.",
                    "# TYPE reranker_tokens_total counter",
                    f"reranker_tokens_total {self._tokens_total}",
                    "# HELP reranker_documents_per_request Candidate documents per rerank request.",
                    "# TYPE reranker_documents_per_request histogram",
                    *self._documents.render("reranker_documents_per_request"),
                    "# HELP reranker_selected_per_request Selected results per rerank request.",
                    "# TYPE reranker_selected_per_request histogram",
                    *self._selected.render("reranker_selected_per_request"),
                ]
            )
        return ("\n".join(lines) + "\n").encode("utf-8")
