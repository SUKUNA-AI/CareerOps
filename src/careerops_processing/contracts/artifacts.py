"""Контракты неизменяемых артефактов Processing v2"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .common import FrozenModel, S3Uri, Sha256, VersionId
from .evidence import RESUME_EVIDENCE_SET_SCHEMA_VERSION
from .filtering import FilterDecision, FilterOutcome
from .manifest import ProcessingInputManifest
from .qualification import REQUIREMENT_QUALIFICATION_SET_SCHEMA_VERSION
from .requirements import REQUIREMENT_SET_SCHEMA_VERSION
from .reranking import EVIDENCE_CANDIDATE_SET_SCHEMA_VERSION
from .scoring import MATCH_DECISION_SCHEMA_VERSION

FILTER_TRACE_SCHEMA_VERSION = "careerops.processing.filter-trace.v1"
P204_RESULT_SCHEMA_VERSION = "careerops.processing.p2-04-result.v2"
P205_RESULT_SCHEMA_VERSION = "careerops.processing.p2-05-result.v1"
P206_RESULT_SCHEMA_VERSION = "careerops.processing.p2-06-result.v1"
P207_RESULT_SCHEMA_VERSION = "careerops.processing.p2-07-result.v1"


class ProcessingArtifactKind(StrEnum):
    INPUT_MANIFEST = "input_manifest"
    FILTER_TRACE = "filter_trace"
    REQUIREMENT_SET = "requirement_set"
    RESUME_EVIDENCE_SET = "resume_evidence_set"
    P2_04_RESULT = "p2_04_result"
    EVIDENCE_CANDIDATE_SET = "evidence_candidate_set"
    P2_05_RESULT = "p2_05_result"
    REQUIREMENT_QUALIFICATION_SET = "requirement_qualification_set"
    P2_06_RESULT = "p2_06_result"
    MATCH_DECISION = "match_decision"
    P2_07_RESULT = "p2_07_result"


class ProcessingArtifactRef(FrozenModel):
    """Ссылка на неизменяемый артефакт с адресацией по содержимому"""

    kind: ProcessingArtifactKind
    schema_version: VersionId
    uri: S3Uri
    sha256: Sha256
    size_bytes: int = Field(gt=0)


class FilterTraceArtifact(FrozenModel):
    """Полная воспроизводимая трассировка решения P2-03"""

    schema_version: VersionId = FILTER_TRACE_SCHEMA_VERSION
    input_fingerprint: Sha256
    manifest: ProcessingInputManifest
    filter_version: VersionId
    decision: FilterDecision

    @model_validator(mode="after")
    def validate_trace(self) -> FilterTraceArtifact:
        if self.input_fingerprint != self.manifest.input_fingerprint():
            raise ValueError("input_fingerprint не совпадает с manifest")
        if self.filter_version != self.manifest.versions.filter_version:
            raise ValueError("filter_version не совпадает с manifest version bundle")
        return self


class P204ResultArtifact(FrozenModel):
    """Воспроизводимая контрольная точка стадий P2-03 и P2-04"""

    schema_version: VersionId = P204_RESULT_SCHEMA_VERSION
    input_fingerprint: Sha256
    manifest: ProcessingInputManifest
    filter_outcome: FilterOutcome
    filter_trace_ref: ProcessingArtifactRef
    requirement_extraction_version: VersionId
    evidence_version: VersionId
    requirement_set_ref: ProcessingArtifactRef | None = None
    resume_evidence_set_ref: ProcessingArtifactRef | None = None

    @model_validator(mode="after")
    def validate_result(self) -> P204ResultArtifact:
        if self.input_fingerprint != self.manifest.input_fingerprint():
            raise ValueError("input_fingerprint не совпадает с manifest")
        if self.filter_trace_ref.kind is not ProcessingArtifactKind.FILTER_TRACE:
            raise ValueError("filter_trace_ref должен ссылаться на FILTER_TRACE")
        if self.filter_trace_ref.schema_version != FILTER_TRACE_SCHEMA_VERSION:
            raise ValueError("filter_trace_ref имеет неподдерживаемую schema version")
        if self.requirement_extraction_version != (
            self.manifest.versions.requirement_extraction_version
        ):
            raise ValueError("requirement extraction version не совпадает с manifest")
        if self.evidence_version != self.manifest.versions.evidence_version:
            raise ValueError("evidence version не совпадает с manifest")

        if self.filter_outcome is FilterOutcome.KEEP:
            if self.requirement_set_ref is None or self.resume_evidence_set_ref is None:
                raise ValueError("KEEP P2-04 result требует requirement и evidence artifacts")
            if self.requirement_set_ref.kind is not ProcessingArtifactKind.REQUIREMENT_SET:
                raise ValueError("requirement_set_ref имеет неверный artifact kind")
            if self.requirement_set_ref.schema_version != REQUIREMENT_SET_SCHEMA_VERSION:
                raise ValueError("requirement_set_ref имеет неподдерживаемую schema version")
            if (
                self.resume_evidence_set_ref.kind
                is not ProcessingArtifactKind.RESUME_EVIDENCE_SET
            ):
                raise ValueError("resume_evidence_set_ref имеет неверный artifact kind")
            if (
                self.resume_evidence_set_ref.schema_version
                != RESUME_EVIDENCE_SET_SCHEMA_VERSION
            ):
                raise ValueError("resume_evidence_set_ref имеет неподдерживаемую schema version")
            return self

        if self.requirement_set_ref is not None or self.resume_evidence_set_ref is not None:
            raise ValueError("EXCLUDE_PROVEN P2-04 result не должен содержать P2-04 artifacts")
        return self


class P205ResultArtifact(FrozenModel):
    """Воспроизводимая контрольная точка P2-05 поверх результата P2-04"""

    schema_version: VersionId = P205_RESULT_SCHEMA_VERSION
    input_fingerprint: Sha256
    manifest: ProcessingInputManifest
    filter_outcome: FilterOutcome
    p2_04_result_ref: ProcessingArtifactRef
    evidence_candidate_set_ref: ProcessingArtifactRef | None = None

    @model_validator(mode="after")
    def validate_result(self) -> P205ResultArtifact:
        if self.input_fingerprint != self.manifest.input_fingerprint():
            raise ValueError("input_fingerprint не совпадает с manifest")
        if self.p2_04_result_ref.kind is not ProcessingArtifactKind.P2_04_RESULT:
            raise ValueError("p2_04_result_ref имеет неверный artifact kind")
        if self.p2_04_result_ref.schema_version != P204_RESULT_SCHEMA_VERSION:
            raise ValueError("p2_04_result_ref имеет неподдерживаемую schema version")

        if self.filter_outcome is FilterOutcome.KEEP:
            if self.manifest.versions.jina is None:
                raise ValueError("KEEP P2-05 result требует JinaVersionBundle в manifest")
            if self.evidence_candidate_set_ref is None:
                raise ValueError("KEEP P2-05 result требует evidence candidate artifact")
            if (
                self.evidence_candidate_set_ref.kind
                is not ProcessingArtifactKind.EVIDENCE_CANDIDATE_SET
            ):
                raise ValueError("evidence_candidate_set_ref имеет неверный artifact kind")
            if (
                self.evidence_candidate_set_ref.schema_version
                != EVIDENCE_CANDIDATE_SET_SCHEMA_VERSION
            ):
                raise ValueError("evidence_candidate_set_ref имеет неподдерживаемую schema version")
            return self

        if self.evidence_candidate_set_ref is not None:
            raise ValueError("EXCLUDE_PROVEN P2-05 result не должен содержать candidate artifact")
        return self


class P206ResultArtifact(FrozenModel):
    """Воспроизводимая контрольная точка deterministic qualification P2-06."""

    schema_version: VersionId = P206_RESULT_SCHEMA_VERSION
    input_fingerprint: Sha256
    manifest: ProcessingInputManifest
    filter_outcome: FilterOutcome
    p2_05_result_ref: ProcessingArtifactRef
    requirement_qualification_set_ref: ProcessingArtifactRef | None = None

    @model_validator(mode="after")
    def validate_result(self) -> P206ResultArtifact:
        if self.input_fingerprint != self.manifest.input_fingerprint():
            raise ValueError("input_fingerprint не совпадает с manifest")
        if self.p2_05_result_ref.kind is not ProcessingArtifactKind.P2_05_RESULT:
            raise ValueError("p2_05_result_ref имеет неверный artifact kind")
        if self.p2_05_result_ref.schema_version != P205_RESULT_SCHEMA_VERSION:
            raise ValueError("p2_05_result_ref имеет неподдерживаемую schema version")

        if self.filter_outcome is FilterOutcome.KEEP:
            ref = self.requirement_qualification_set_ref
            if ref is None:
                raise ValueError("KEEP P2-06 result требует qualification artifact")
            if ref.kind is not ProcessingArtifactKind.REQUIREMENT_QUALIFICATION_SET:
                raise ValueError("qualification ref имеет неверный artifact kind")
            if ref.schema_version != REQUIREMENT_QUALIFICATION_SET_SCHEMA_VERSION:
                raise ValueError("qualification ref имеет неподдерживаемую schema version")
            return self

        if self.requirement_qualification_set_ref is not None:
            raise ValueError(
                "EXCLUDE_PROVEN P2-06 result не должен содержать qualification artifact"
            )
        return self


class P207ResultArtifact(FrozenModel):
    """Финальный immutable checkpoint Processing matching/scoring P2-07."""

    schema_version: VersionId = P207_RESULT_SCHEMA_VERSION
    input_fingerprint: Sha256
    manifest: ProcessingInputManifest
    filter_outcome: FilterOutcome
    p2_06_result_ref: ProcessingArtifactRef
    match_decision_ref: ProcessingArtifactRef

    @model_validator(mode="after")
    def validate_result(self) -> P207ResultArtifact:
        if self.input_fingerprint != self.manifest.input_fingerprint():
            raise ValueError("input_fingerprint не совпадает с manifest")
        if self.p2_06_result_ref.kind is not ProcessingArtifactKind.P2_06_RESULT:
            raise ValueError("p2_06_result_ref имеет неверный artifact kind")
        if self.p2_06_result_ref.schema_version != P206_RESULT_SCHEMA_VERSION:
            raise ValueError("p2_06_result_ref имеет неподдерживаемую schema version")
        if self.match_decision_ref.kind is not ProcessingArtifactKind.MATCH_DECISION:
            raise ValueError("match_decision_ref имеет неверный artifact kind")
        if self.match_decision_ref.schema_version != MATCH_DECISION_SCHEMA_VERSION:
            raise ValueError("match_decision_ref имеет неподдерживаемую schema version")
        return self
