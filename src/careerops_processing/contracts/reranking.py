"""Контракты выбора evidence-кандидатов для P2-05"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .common import FrozenModel, NonEmptyStr, Sha256, VersionId
from .versions import JinaVersionBundle

EVIDENCE_CANDIDATE_SET_SCHEMA_VERSION = "careerops.processing.evidence-candidate-set.v1"


class RequirementSelectionState(StrEnum):
    """Состояние выбора evidence для одного requirement"""

    RANKED = "ranked"
    NO_EVIDENCE = "no_evidence"
    SKIPPED_NOT_REQUIRED = "skipped_not_required"


class EvidenceCandidate(FrozenModel):
    """Один evidence-кандидат после listwise reranking"""

    evidence_id: NonEmptyStr
    rank: int = Field(ge=1)
    relevance_score: float = Field(allow_inf_nan=False)


class RequirementEvidenceCandidates(FrozenModel):
    """Результат выбора evidence-кандидатов для одного requirement"""

    requirement_id: NonEmptyStr
    state: RequirementSelectionState
    query_text: NonEmptyStr | None = None
    pool_evidence_ids: tuple[NonEmptyStr, ...] = ()
    pool_render_sha256: Sha256 | None = None
    candidates: tuple[EvidenceCandidate, ...] = ()

    @model_validator(mode="after")
    def validate_selection(self) -> RequirementEvidenceCandidates:
        pool_ids = list(self.pool_evidence_ids)
        if len(pool_ids) != len(set(pool_ids)):
            raise ValueError("evidence candidate pool ids must be unique")

        candidate_ids = [item.evidence_id for item in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("selected evidence candidate ids must be unique")
        if any(item not in set(pool_ids) for item in candidate_ids):
            raise ValueError("selected evidence candidate must belong to candidate pool")

        expected_ranks = list(range(1, len(self.candidates) + 1))
        if [item.rank for item in self.candidates] != expected_ranks:
            raise ValueError("evidence candidate ranks must be consecutive from one")

        if self.state is RequirementSelectionState.RANKED:
            if self.query_text is None:
                raise ValueError("ranked requirement requires query_text")
            if not self.pool_evidence_ids or self.pool_render_sha256 is None:
                raise ValueError("ranked requirement requires a non-empty candidate pool")
            if not self.candidates:
                raise ValueError("ranked requirement requires selected evidence candidates")
            return self

        if self.candidates:
            raise ValueError("non-ranked requirement must not contain selected candidates")
        if self.state is RequirementSelectionState.NO_EVIDENCE:
            if self.query_text is None:
                raise ValueError("no-evidence requirement requires query_text")
            if self.pool_evidence_ids or self.pool_render_sha256 is not None:
                raise ValueError("no-evidence requirement must have an empty candidate pool")
            return self

        if self.query_text is not None:
            raise ValueError("skipped NOT_REQUIRED requirement must not contain query_text")
        if self.pool_evidence_ids or self.pool_render_sha256 is not None:
            raise ValueError("skipped NOT_REQUIRED requirement must not contain candidate pool")
        return self


class EvidenceCandidateSet(FrozenModel):
    """Все P2-05 evidence-кандидаты одной vacancy × resume пары"""

    schema_version: VersionId = EVIDENCE_CANDIDATE_SET_SCHEMA_VERSION
    input_fingerprint: Sha256
    requirement_set_sha256: Sha256
    resume_evidence_set_sha256: Sha256
    jina: JinaVersionBundle
    selections: tuple[RequirementEvidenceCandidates, ...] = ()

    @model_validator(mode="after")
    def validate_unique_requirements(self) -> EvidenceCandidateSet:
        requirement_ids = [item.requirement_id for item in self.selections]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirement ids must be unique within EvidenceCandidateSet")
        return self
