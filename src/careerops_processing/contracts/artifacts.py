"""Контракты неизменяемых артефактов Processing v2"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .common import FrozenModel, S3Uri, Sha256, VersionId
from .filtering import FilterDecision, FilterOutcome
from .manifest import ProcessingInputManifest

FILTER_TRACE_SCHEMA_VERSION = "careerops.processing.filter-trace.v1"
P204_RESULT_SCHEMA_VERSION = "careerops.processing.p2-04-result.v1"


class ProcessingArtifactKind(StrEnum):
    INPUT_MANIFEST = "input_manifest"
    FILTER_TRACE = "filter_trace"
    REQUIREMENT_SET = "requirement_set"
    RESUME_EVIDENCE_SET = "resume_evidence_set"
    P2_04_RESULT = "p2_04_result"


class ProcessingArtifactRef(FrozenModel):
    """Content-addressed ссылка на неизменяемый артефакт Processing"""

    kind: ProcessingArtifactKind
    schema_version: VersionId
    uri: S3Uri
    sha256: Sha256
    size_bytes: int = Field(gt=0)


class FilterTraceArtifact(FrozenModel):
    """Полный воспроизводимый trace решения P2-03"""

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
    """Финальный воспроизводимый checkpoint текущего runtime horizon P2-03 + P2-04"""

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
            if (
                self.resume_evidence_set_ref.kind
                is not ProcessingArtifactKind.RESUME_EVIDENCE_SET
            ):
                raise ValueError("resume_evidence_set_ref имеет неверный artifact kind")
            return self

        if self.requirement_set_ref is not None or self.resume_evidence_set_ref is not None:
            raise ValueError("EXCLUDE_PROVEN P2-04 result не должен содержать P2-04 artifacts")
        return self
