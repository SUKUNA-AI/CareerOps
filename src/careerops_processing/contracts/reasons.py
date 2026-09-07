"""Namespaced reason-code contract shared by filtering, matching and policy."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints

ReasonCode = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
        min_length=3,
        max_length=160,
    ),
]


class ReasonNamespace(StrEnum):
    FILTER = "filter"
    POLICY = "policy"
    SOURCE = "source"
    REQUIREMENTS = "requirements"
    EVIDENCE = "evidence"
    ROLE = "role"
    MATCH = "match"


INITIAL_REASON_CODES: tuple[ReasonCode, ...] = (
    "filter.primary_role_disjoint",
    "policy.hard_location_conflict",
    "policy.forbidden_context",
    "source.vacancy_unavailable",
    "requirements.critical_contradiction",
    "requirements.mandatory_not_evidenced",
    "requirements.importance_ambiguous",
    "evidence.scope_unresolved",
    "role.ambiguous",
    "match.policy_satisfied",
)
