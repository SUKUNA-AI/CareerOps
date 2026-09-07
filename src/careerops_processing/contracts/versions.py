"""Pinned Processing v2 implementation/model version bundles."""

from __future__ import annotations

from pydantic import Field

from .common import FrozenModel, NonEmptyStr, VersionId


class JinaVersionBundle(FrozenModel):
    """All inputs that may change listwise Jina outputs."""

    model_id: NonEmptyStr
    model_revision: NonEmptyStr
    model_code_revision: NonEmptyStr
    tokenizer_revision: NonEmptyStr
    runtime_backend: NonEmptyStr
    dtype_or_quantization: NonEmptyStr
    rendering_version: VersionId
    selection_version: VersionId
    block_protocol: VersionId
    token_budget: int = Field(gt=0)


class ProcessingVersionBundle(FrozenModel):
    """Versions that define deterministic semantics of one processing evaluation."""

    pipeline_version: VersionId
    dictionary_version: VersionId
    requirement_extraction_version: VersionId
    evidence_version: VersionId
    qualification_version: VersionId
    scoring_version: VersionId
    calibration_version: VersionId
    jina: JinaVersionBundle | None = None
