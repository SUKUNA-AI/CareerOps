"""Namespaced reason-code contract для filtering, matching и policy"""

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
    "filter.seniority_forbidden",
    "filter.management_forbidden",
    "filter.experience_gap_exceeded",
    "filter.proven_exclusion",
    "policy.hard_location_conflict",
    "policy.hard_work_format_conflict",
    "policy.relocation_required",
    "policy.forbidden_context",
    "source.vacancy_unavailable",
    "requirements.critical_contradiction",
    "requirements.mandatory_not_evidenced",
    "requirements.importance_ambiguous",
    "requirements.source_conflict",
    "requirements.explicit_contradiction",
    "requirements.direct_support",
    "requirements.threshold_not_evidenced",
    "requirements.threshold_scope_unresolved",
    "requirements.subject_not_evidenced",
    "requirements.semantic_alignment_unresolved",
    "requirements.condition_unresolved",
    "evidence.scope_unresolved",
    "evidence.corpus_empty",
    "evidence.date_scope_unresolved",
    "evidence.selection_incomplete",
    "role.ambiguous",
    "match.policy_satisfied",
    "match.policy_not_satisfied",
    "match.policy_uncertain",
    "match.calibration_unset",
    "match.scoring_signal_unavailable",
)
