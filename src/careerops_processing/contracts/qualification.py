"""Deterministic requirement qualification contracts for P2-06."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from .common import FrozenModel, NonEmptyStr, Sha256, VersionId
from .reasons import ReasonCode

REQUIREMENT_QUALIFICATION_SET_SCHEMA_VERSION = (
    "careerops.processing.requirement-qualification-set.v1"
)


class RequirementQualificationState(StrEnum):
    """Canonical P2-06 state for one applicable requirement."""

    MATCHED = "matched"
    NOT_EVIDENCED = "not_evidenced"
    UNKNOWN = "unknown"
    CONTRADICTED = "contradicted"


class SupportBounds(FrozenModel):
    """Deterministic lower/upper support bounds, not probabilities."""

    lower: Decimal = Field(ge=0, le=1)
    upper: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_order(self) -> SupportBounds:
        if self.lower > self.upper:
            raise ValueError("support lower bound must be <= upper bound")
        return self


class RequirementQualification(FrozenModel):
    """Explainable P2-06 result for one requirement."""

    requirement_id: NonEmptyStr
    state: RequirementQualificationState
    support: SupportBounds
    selection_complete: bool
    ranked_evidence_ids: tuple[NonEmptyStr, ...] = ()
    supporting_evidence_ids: tuple[NonEmptyStr, ...] = ()
    contradicting_evidence_ids: tuple[NonEmptyStr, ...] = ()
    ambiguous_evidence_ids: tuple[NonEmptyStr, ...] = ()
    reason_codes: tuple[ReasonCode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_state(self) -> RequirementQualification:
        for values in (
            self.ranked_evidence_ids,
            self.supporting_evidence_ids,
            self.contradicting_evidence_ids,
            self.ambiguous_evidence_ids,
        ):
            if len(values) != len(set(values)):
                raise ValueError("qualification evidence ids must be unique")

        ranked = set(self.ranked_evidence_ids)
        support_ids = set(self.supporting_evidence_ids)
        contradiction_ids = set(self.contradicting_evidence_ids)
        ambiguous_ids = set(self.ambiguous_evidence_ids)
        semantic_ids = support_ids | contradiction_ids | ambiguous_ids
        if not semantic_ids <= ranked:
            raise ValueError("qualified evidence ids must belong to ranked evidence")
        if (
            support_ids & contradiction_ids
            or support_ids & ambiguous_ids
            or contradiction_ids & ambiguous_ids
        ):
            raise ValueError("supporting, contradicting and ambiguous evidence must be disjoint")

        zero = Decimal("0")
        one = Decimal("1")
        if self.state is RequirementQualificationState.MATCHED:
            if self.support != SupportBounds(lower=one, upper=one):
                raise ValueError("MATCHED requires support bounds [1, 1]")
            if not self.supporting_evidence_ids:
                raise ValueError("MATCHED requires supporting evidence")
            if self.contradicting_evidence_ids:
                raise ValueError("MATCHED cannot contain contradicting evidence")
            return self

        if self.state is RequirementQualificationState.CONTRADICTED:
            if self.support != SupportBounds(lower=zero, upper=zero):
                raise ValueError("CONTRADICTED requires support bounds [0, 0]")
            if not self.contradicting_evidence_ids:
                raise ValueError("CONTRADICTED requires contradicting evidence")
            if self.supporting_evidence_ids:
                raise ValueError("CONTRADICTED cannot contain supporting evidence")
            return self

        if self.support != SupportBounds(lower=zero, upper=one):
            raise ValueError("UNKNOWN/NOT_EVIDENCED require support bounds [0, 1]")
        return self


class RequirementGroupQualification(FrozenModel):
    """Aggregated support bounds for one requirement graph node."""

    group_id: NonEmptyStr
    support: SupportBounds
    reason_codes: tuple[ReasonCode, ...] = ()


class RequirementQualificationSet(FrozenModel):
    """All deterministic P2-06 qualifications for one vacancy x resume pair."""

    schema_version: VersionId = REQUIREMENT_QUALIFICATION_SET_SCHEMA_VERSION
    input_fingerprint: Sha256
    requirement_set_sha256: Sha256
    resume_evidence_set_sha256: Sha256
    evidence_candidate_set_sha256: Sha256
    qualification_version: VersionId
    evaluations: tuple[RequirementQualification, ...] = ()
    groups: tuple[RequirementGroupQualification, ...] = ()
    ignored_requirement_ids: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def validate_ids(self) -> RequirementQualificationSet:
        evaluation_ids = [item.requirement_id for item in self.evaluations]
        group_ids = [item.group_id for item in self.groups]
        if len(evaluation_ids) != len(set(evaluation_ids)):
            raise ValueError("qualification requirement ids must be unique")
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("qualification group ids must be unique")
        if len(self.ignored_requirement_ids) != len(set(self.ignored_requirement_ids)):
            raise ValueError("ignored requirement ids must be unique")
        if set(evaluation_ids) & set(self.ignored_requirement_ids):
            raise ValueError("ignored requirements must not have qualification evaluations")
        return self
