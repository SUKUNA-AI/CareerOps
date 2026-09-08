"""Контракты структурированных resume evidence для P2-04"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .common import FrozenModel, NonEmptyStr, Sha256, VersionId
from .semantics import SemanticPolarity, SemanticSourceRef, SemanticSubject, SemanticTimeSpan

RESUME_EVIDENCE_SET_SCHEMA_VERSION = "careerops.processing.resume-evidence-set.v1"


class EvidenceKind(StrEnum):
    SKILL = "skill"
    EXPERIENCE = "experience"
    EXPERIENCE_SUMMARY = "experience_summary"
    PROJECT = "project"
    SUMMARY = "summary"
    EDUCATION = "education"
    LANGUAGE = "language"
    PREFERENCE = "preference"


class EvidenceActorScope(StrEnum):
    SELF = "self"
    TEAM = "team"
    PROJECT = "project"
    MENTION = "mention"
    UNKNOWN = "unknown"


class EvidenceContext(StrEnum):
    COMMERCIAL = "commercial"
    PROJECT = "project"
    SUMMARY = "summary"
    SKILL_LIST = "skill_list"
    EDUCATION = "education"
    LANGUAGE = "language"
    PREFERENCE = "preference"
    UNKNOWN = "unknown"


class EvidenceStrength(StrEnum):
    DIRECT = "direct"
    SUPPORTED = "supported"
    MENTION = "mention"
    UNKNOWN = "unknown"


class ResumeEvidence(FrozenModel):
    """Одна evidence unit с actor scope, context, polarity и provenance"""

    evidence_id: NonEmptyStr
    kind: EvidenceKind
    statement: NonEmptyStr
    subjects: tuple[SemanticSubject, ...] = ()
    activity: NonEmptyStr | None = None
    actor_scope: EvidenceActorScope = EvidenceActorScope.UNKNOWN
    context: EvidenceContext = EvidenceContext.UNKNOWN
    polarity: SemanticPolarity = SemanticPolarity.POSITIVE
    strength: EvidenceStrength = EvidenceStrength.UNKNOWN
    time_span: SemanticTimeSpan | None = None
    source_refs: tuple[SemanticSourceRef, ...] = Field(min_length=1)


class ResumeEvidenceSet(FrozenModel):
    """Детерминированный evidence набор одной resume version"""

    schema_version: VersionId = RESUME_EVIDENCE_SET_SCHEMA_VERSION
    source_key: NonEmptyStr
    account_key: NonEmptyStr
    source_entity_id: NonEmptyStr
    semantic_content_hash: Sha256
    normalized_schema_version: VersionId
    normalization_version: VersionId
    dictionary_version: VersionId
    evidence_version: VersionId
    evidence: tuple[ResumeEvidence, ...] = ()

    @model_validator(mode="after")
    def validate_unique_ids(self) -> ResumeEvidenceSet:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence ids must be unique within ResumeEvidenceSet")
        return self
