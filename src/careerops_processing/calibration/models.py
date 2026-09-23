"""Calibration-v1 contracts for weak-gold Astra labels and stage predictions."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from careerops_processing.contracts.scoring import (
    validate_non_overlapping_requirement_component_weights,
)


class FrozenCalibrationModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProjectionModel(BaseModel):
    """Projection of external calibration input; unrelated source fields are ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class AstraDecision(StrEnum):
    APPLICATION_CANDIDATE = "APPLICATION_CANDIDATE"
    REVIEW = "REVIEW"
    SKIP = "SKIP"


class AstraConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class DataSufficiency(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class RequirementImportanceLabel(StrEnum):
    MANDATORY = "MANDATORY"
    PREFERRED = "PREFERRED"
    CONTEXT = "CONTEXT"
    UNKNOWN = "UNKNOWN"


class AstraRequirementStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"


class QualificationStateLabel(StrEnum):
    MATCHED = "MATCHED"
    NOT_EVIDENCED = "NOT_EVIDENCED"
    UNKNOWN = "UNKNOWN"
    CONTRADICTED = "CONTRADICTED"


class SplitName(StrEnum):
    CALIBRATION = "calibration"
    VALIDATION = "validation"
    HOLDOUT = "holdout"


class P203Outcome(StrEnum):
    KEEP = "KEEP"
    EXCLUDE_PROVEN = "EXCLUDE_PROVEN"


class AstraEvidence(FrozenCalibrationModel):
    ref: str = Field(min_length=1)
    snippet: str


class AstraRequirement(FrozenCalibrationModel):
    requirement_text: str = Field(min_length=1)
    importance: RequirementImportanceLabel
    status: AstraRequirementStatus
    evidence: tuple[AstraEvidence, ...] = ()


class AstraAnnotation(FrozenCalibrationModel):
    pair_id: str = Field(min_length=1)
    annotation_policy_version: str = Field(min_length=1)
    annotation_source: str = Field(min_length=1)
    annotator_model: str | None = None
    decision: AstraDecision
    confidence: AstraConfidence
    data_sufficiency: DataSufficiency
    role_fit: int = Field(ge=0, le=4)
    seniority_fit: int = Field(ge=0, le=4)
    requirements: tuple[AstraRequirement, ...] = ()
    hard_reject_reasons: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    overall_reason: str = Field(min_length=1)


class CalibrationResumeProjection(ProjectionModel):
    calibration_resume_key: str = Field(min_length=1)
    source_resume_id: str = Field(min_length=1)


class CalibrationVacancyProjection(ProjectionModel):
    source_vacancy_id: str = Field(min_length=1)


class CalibrationBatchPairProjection(ProjectionModel):
    pair_id: str = Field(min_length=1)
    vacancy: CalibrationVacancyProjection


class CalibrationBatchProjection(ProjectionModel):
    batch_id: str = Field(min_length=1)
    annotation_policy_version: str = Field(min_length=1)
    resume: CalibrationResumeProjection
    pairs: tuple[CalibrationBatchPairProjection, ...] = Field(min_length=1)


class PairMetadata(FrozenCalibrationModel):
    pair_id: str = Field(min_length=1)
    source_vacancy_id: str = Field(min_length=1)
    resume_key: str = Field(min_length=1)
    source_resume_id: str = Field(min_length=1)


class SplitAssignment(FrozenCalibrationModel):
    pair_id: str = Field(min_length=1)
    source_vacancy_id: str = Field(min_length=1)
    split: SplitName


class CalibrationManifest(FrozenCalibrationModel):
    schema_version: str = "careerops.calibration-manifest.v1"
    dataset_id: str = Field(min_length=1)
    label_tier: str = "weak_gold"
    annotation_source: str = "astra"
    annotation_policy_version: str = Field(min_length=1)
    annotator_model: str | None = None
    provenance_complete: bool
    annotations_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    batches_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    split_seed: str = Field(min_length=1)
    calibration_fraction: Decimal = Field(gt=0, lt=1)
    validation_fraction: Decimal = Field(gt=0, lt=1)
    holdout_fraction: Decimal = Field(gt=0, lt=1)
    pair_count: int = Field(gt=0)
    unique_vacancies: int = Field(gt=0)
    unique_resumes: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_fractions(self) -> CalibrationManifest:
        total = self.calibration_fraction + self.validation_fraction + self.holdout_fraction
        if total != Decimal("1"):
            raise ValueError("calibration split fractions must sum to exactly 1")
        return self


class P203Prediction(FrozenCalibrationModel):
    pair_id: str = Field(min_length=1)
    outcome: P203Outcome
    reason_codes: tuple[str, ...] = ()


class P204RequirementPrediction(FrozenCalibrationModel):
    requirement_id: str = Field(min_length=1)
    gold_requirement_index: int | None = Field(default=None, ge=0)
    evidence_refs: tuple[str, ...] = ()


class P204Prediction(FrozenCalibrationModel):
    pair_id: str = Field(min_length=1)
    requirements: tuple[P204RequirementPrediction, ...] = ()


class P205Prediction(FrozenCalibrationModel):
    pair_id: str = Field(min_length=1)
    gold_requirement_index: int = Field(ge=0)
    ranked_evidence_refs: tuple[str, ...] = ()


class P206Prediction(FrozenCalibrationModel):
    pair_id: str = Field(min_length=1)
    gold_requirement_index: int = Field(ge=0)
    state: QualificationStateLabel


class P207Prediction(FrozenCalibrationModel):
    pair_id: str = Field(min_length=1)
    decision: AstraDecision


class ScoreInterval(FrozenCalibrationModel):
    lower: Decimal = Field(ge=0, le=100)
    upper: Decimal = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_order(self) -> ScoreInterval:
        if self.lower > self.upper:
            raise ValueError("score interval lower must be <= upper")
        return self


class SupportInterval(FrozenCalibrationModel):
    lower: Decimal = Field(ge=0, le=1)
    upper: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_order(self) -> SupportInterval:
        if self.lower > self.upper:
            raise ValueError("support interval lower must be <= upper")
        return self


class P207ReplayCase(FrozenCalibrationModel):
    pair_id: str = Field(min_length=1)
    components: dict[str, ScoreInterval]
    mandatory_support: SupportInterval
    critical_conflict: bool = False


class P207PolicyCandidate(FrozenCalibrationModel):
    candidate_id: str = Field(min_length=1)
    candidate_min_score: Decimal = Field(ge=0, le=100)
    mandatory_min_support: Decimal = Field(ge=0, le=1)
    component_weights: dict[str, Decimal]

    @model_validator(mode="after")
    def validate_weights(self) -> P207PolicyCandidate:
        if not self.component_weights:
            raise ValueError("component_weights must not be empty")
        if any(weight < 0 for weight in self.component_weights.values()):
            raise ValueError("component weights must be non-negative")
        if sum(self.component_weights.values(), Decimal("0")) <= 0:
            raise ValueError("component_weights must contain positive total weight")
        validate_non_overlapping_requirement_component_weights(self.component_weights)
        return self
