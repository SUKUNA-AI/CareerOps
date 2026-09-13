"""Correlation context shared between Processing selector and HTTP transport."""

from __future__ import annotations

from contextvars import ContextVar

reranker_request_id: ContextVar[str | None] = ContextVar(
    "careerops_reranker_request_id",
    default=None,
)
