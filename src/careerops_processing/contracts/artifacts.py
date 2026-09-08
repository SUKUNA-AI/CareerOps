"""Контракты неизменяемых артефактов Processing v2"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .common import FrozenModel, S3Uri, Sha256, VersionId
from .filtering import FilterDecision
from .manifest import ProcessingInputManifest

FILTER_TRACE_SCHEMA_VERSION = "careerops.processing.filter-trace.v1"


class ProcessingArtifactKind(StrEnum):
    INPUT_MANIFEST = "input_manifest"
    FILTER_TRACE = "filter_trace"


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
