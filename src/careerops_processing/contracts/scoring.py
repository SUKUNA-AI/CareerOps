"""Deterministic scoring and policy-decision contracts for P2-07."""

from __future__ import annotations

import json
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from .common import FrozenModel, NonEmptyStr, Sha256, VersionId
from .policy import TargetPolicy
from .reasons import ReasonCode

MATCH_DECISION_SCHEMA_VERSION = "careerops.processing.match-decision.v1"

SCORING_COMPONENT_KEYS = frozenset(
    {
        "role_fit",
        "mandatory_coverage",
        "preferred_coverage",
        "optional_coverage",
        "responsibility_fit",
        "technology_fit",
        "experience_fit",
        "seniority_fit",
        "domain_fit",
        "location_fit",
        "work_format_fit",
        "education_fit",
        "language_fit",
        "work_condition_fit",
        "other_fit",
    }
)


class MatchDecision(StrEnum):
    SKIP = "skip"
    REVIEW = "review"
    APPLICATION_CANDIDATE = "application_candidate"


class ScoreBounds(FrozenModel):
    """Bounded ranking utility interval in 0..100, not a probability."""

    lower: Decimal = Field(ge=0, le=100)
    upper: Decimal = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_order(self) -> ScoreBounds:
        if self.lower > self.upper:
            raise ValueError("score lower bound must be <= upper bound")
        return self


class ScoringComponent(FrozenModel):
    """One explainable deterministic score component."""

    key: NonEmptyStr
    lower: Decimal = Field(ge=0, le=100)
    upper: Decimal = Field(ge=0, le=100)
    weight: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> ScoringComponent:
        if self.lower > self.upper:
            raise ValueError("component lower bound must be <= upper bound")
        if self.key not in SCORING_COMPONENT_KEYS:
            raise ValueError(f"unsupported scoring component: {self.key}")
        return self


class ScoringPolicy(FrozenModel):
    """Calibrated, versioned P2-07 policy. It is mandatory after calibration cutover."""

    schema_version: int = Field(default=1, ge=1, le=1)
    calibration_version: VersionId
    candidate_min_score: Decimal = Field(ge=0, le=100)
    mandatory_min_support: Decimal = Field(ge=0, le=1)
    component_weights: dict[NonEmptyStr, Decimal]
    candidate_ttl_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_weights(self) -> ScoringPolicy:
        if not self.component_weights:
            raise ValueError("scoring component_weights must not be empty")
        unknown = set(self.component_weights) - SCORING_COMPONENT_KEYS
        if unknown:
            raise ValueError(f"unsupported scoring components: {sorted(unknown)}")
        if any(weight < 0 for weight in self.component_weights.values()):
            raise ValueError("scoring component weights must be non-negative")
        if sum(self.component_weights.values(), Decimal("0")) <= 0:
            raise ValueError("scoring component weights must contain positive weight")
        return self

    @classmethod
    def from_target_policy(cls, policy: TargetPolicy) -> ScoringPolicy | None:
        content = policy.parsed_content()
        section = content.get("scoring")
        if section is None:
            return None
        if not isinstance(section, dict):
            raise ValueError("target policy scoring section must be a JSON object")
        payload = json.dumps(
            section,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return cls.model_validate_json(payload)


class MatchDecisionBundle(FrozenModel):
    """Replayable P2-07 decision over stored deterministic qualification."""

    schema_version: VersionId = MATCH_DECISION_SCHEMA_VERSION
    input_fingerprint: Sha256
    requirement_qualification_set_sha256: Sha256 | None = None
    scoring_version: VersionId
    calibration_version: VersionId
    policy_version: VersionId
    decision: MatchDecision
    score: ScoreBounds
    deterministic_score: Decimal = Field(ge=0, le=100)
    components: tuple[ScoringComponent, ...] = ()
    critical_conflict_requirement_ids: tuple[NonEmptyStr, ...] = ()
    unknown_requirement_ids: tuple[NonEmptyStr, ...] = ()
    not_evidenced_requirement_ids: tuple[NonEmptyStr, ...] = ()
    reason_codes: tuple[ReasonCode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_decision(self) -> MatchDecisionBundle:
        if self.deterministic_score != self.score.lower:
            raise ValueError("deterministic_score must equal conservative score lower bound")

        component_keys = [item.key for item in self.components]
        if len(component_keys) != len(set(component_keys)):
            raise ValueError("match decision scoring component keys must be unique")

        requirement_sets: list[set[str]] = []
        for values in (
            self.critical_conflict_requirement_ids,
            self.unknown_requirement_ids,
            self.not_evidenced_requirement_ids,
        ):
            if len(values) != len(set(values)):
                raise ValueError("decision requirement ids must be unique")
            requirement_sets.append(set(values))
        if (
            requirement_sets[0] & requirement_sets[1]
            or requirement_sets[0] & requirement_sets[2]
            or requirement_sets[1] & requirement_sets[2]
        ):
            raise ValueError("decision requirement state buckets must be disjoint")

        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("match decision reason codes must be unique")

        if self.decision is MatchDecision.APPLICATION_CANDIDATE:
            if self.critical_conflict_requirement_ids:
                raise ValueError("APPLICATION_CANDIDATE cannot contain critical conflicts")
        return self
