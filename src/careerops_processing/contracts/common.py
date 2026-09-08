"""Общие immutable типы границы сервиса Processing v2"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Generic, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
S3Uri = Annotated[str, StringConstraints(pattern=r"^s3://[^/]+/.+")]
VersionId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class FrozenModel(BaseModel):
    """Базовая модель immutable cross-component contracts"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EntityType(StrEnum):
    VACANCY = "vacancy"
    RESUME = "resume"


class ValueState(StrEnum):
    """Состояние присутствия и интерпретации source-backed value"""

    KNOWN = "known"
    NOT_PROVIDED = "not_provided"
    EXPLICIT_NONE = "explicit_none"
    UNKNOWN_PARSE = "unknown_parse"


T = TypeVar("T")


class SourceValue(FrozenModel, Generic[T]):
    """Source-backed value без схлопывания missing/unknown в falsey значение"""

    state: ValueState
    value: T | None = None

    @model_validator(mode="after")
    def validate_state_value(self) -> SourceValue[T]:
        if self.state is ValueState.KNOWN and self.value is None:
            raise ValueError("KNOWN source value requires a non-null value")
        if self.state is not ValueState.KNOWN and self.value is not None:
            raise ValueError("non-KNOWN source value must not carry a value")
        return self


class RawObservationRef(FrozenModel):
    """Точная immutable RAW observation, из которой получены normalized data"""

    raw_uri: S3Uri
    raw_sha256: Sha256
    observed_at: datetime

    @model_validator(mode="after")
    def normalize_observed_at(self) -> RawObservationRef:
        value = self.observed_at
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        normalized = value.astimezone(UTC)
        if normalized != value:
            object.__setattr__(self, "observed_at", normalized)
        return self


class DataQualityStatus(StrEnum):
    CLEAN = "clean"
    WARNING = "warning"
    BLOCKED = "blocked"


class NormalizedRef(FrozenModel):
    """Стабильная ссылка на одну точную normalized version вакансии или резюме"""

    entity_type: EntityType
    source_key: NonEmptyStr
    source_entity_id: NonEmptyStr
    account_key: NonEmptyStr | None = None

    raw: RawObservationRef

    normalized_uri: S3Uri
    normalized_sha256: Sha256
    semantic_content_hash: Sha256

    schema_version: VersionId
    normalization_version: VersionId
    dictionary_version: VersionId
    materialization_key: NonEmptyStr

    processing_ready: bool
    dq_status: DataQualityStatus

    @model_validator(mode="after")
    def validate_identity_scope(self) -> NormalizedRef:
        if self.entity_type is EntityType.RESUME and self.account_key is None:
            raise ValueError("resume NormalizedRef requires account_key")
        if self.entity_type is EntityType.VACANCY and self.account_key is not None:
            raise ValueError("vacancy NormalizedRef must not contain account_key")
        if self.processing_ready and self.dq_status is DataQualityStatus.BLOCKED:
            raise ValueError("processing_ready ref cannot have blocked DQ status")
        return self


class SourceLabel(FrozenModel):
    """Source или dictionary-backed code/label без matching semantics"""

    key: NonEmptyStr
    label: NonEmptyStr | None = None
    source_code: NonEmptyStr | None = None


class SourceTextRef(FrozenModel):
    """Связь normalized text с точным source location"""

    source_path: NonEmptyStr
    locator: NonEmptyStr
    quote: NonEmptyStr


class TextBlock(FrozenModel):
    """Значимый source text block со стабильным provenance внутри version"""

    block_id: NonEmptyStr
    text: NonEmptyStr
    ordinal: int = Field(ge=0)
    heading: NonEmptyStr | None = None
    section_hint: NonEmptyStr | None = None
    source_ref: SourceTextRef
