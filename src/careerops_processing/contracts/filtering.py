"""Immutable contracts high-recall deterministic admission filter"""

from __future__ import annotations

import json
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from .common import FrozenModel, NonEmptyStr, VersionId
from .policy import TargetPolicy
from .reasons import ReasonCode


class FilterOutcome(StrEnum):
    KEEP = "keep"
    EXCLUDE_PROVEN = "exclude_proven"


class RoleFamily(StrEnum):
    ML_ENGINEERING = "ml_engineering"
    DATA_SCIENCE = "data_science"
    AI_LLM = "ai_llm"
    COMPUTER_VISION = "computer_vision"
    MLOPS = "mlops"
    ML_RESEARCH = "ml_research"
    RECOMMENDATION_RANKING = "recommendation_ranking"
    DATA_ENGINEERING = "data_engineering"
    PYTHON_BACKEND = "python_backend"
    CPP = "cpp"
    DATA_ANALYTICS = "data_analytics"
    JAVA_BACKEND = "java_backend"
    FRONTEND = "frontend"
    DEVOPS = "devops"
    QA = "qa"
    PRODUCT_MANAGEMENT = "product_management"
    PROJECT_MANAGEMENT = "project_management"
    BUSINESS_ANALYSIS = "business_analysis"
    SYSTEM_ANALYSIS = "system_analysis"
    DBA = "dba"


class SeniorityLevel(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MIDDLE = "middle"
    SENIOR = "senior"
    LEAD = "lead"
    PRINCIPAL = "principal"
    HEAD = "head"


class WorkFormat(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class FilterPolicy(FrozenModel):
    """Типизированная filtering section одного immutable TargetPolicy snapshot"""

    schema_version: int = Field(default=1, ge=1, le=1)
    allowed_primary_roles: tuple[RoleFamily, ...] = ()
    forbidden_primary_roles: tuple[RoleFamily, ...] = ()
    allowed_work_formats: tuple[WorkFormat, ...] = ()
    allowed_area_ids: tuple[NonEmptyStr, ...] = ()
    allowed_area_names: tuple[NonEmptyStr, ...] = ()
    forbidden_seniority: tuple[SeniorityLevel, ...] = ()
    management_allowed: bool = True
    relocation_allowed: bool = True
    maximum_experience_gap_years: Decimal | None = Field(default=None, ge=0)
    forbidden_context_terms: tuple[NonEmptyStr, ...] = ()
    exclude_unavailable: bool = True

    @model_validator(mode="after")
    def validate_roles(self) -> FilterPolicy:
        overlap = set(self.allowed_primary_roles) & set(self.forbidden_primary_roles)
        if overlap:
            values = ", ".join(sorted(item.value for item in overlap))
            raise ValueError(f"role families cannot be both allowed and forbidden: {values}")
        return self

    @classmethod
    def from_target_policy(cls, policy: TargetPolicy) -> FilterPolicy:
        content = policy.parsed_content()
        section = content.get("filtering")
        if section is None:
            return cls()
        if not isinstance(section, dict):
            raise ValueError("target policy filtering section must be a JSON object")
        payload = json.dumps(
            section,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return cls.model_validate_json(payload)


class FilterEvidence(FrozenModel):
    """Точный source-backed факт, доказывающий одно exclusion rule"""

    source_path: NonEmptyStr
    value: NonEmptyStr
    source_locator: NonEmptyStr | None = None
    quote: NonEmptyStr | None = None


class ProvenExclusion(FrozenModel):
    """Стабильное срабатывание rule с evidence и разрешившей его policy version"""

    rule_id: NonEmptyStr
    reason_code: ReasonCode
    policy_version: VersionId
    evidence: tuple[FilterEvidence, ...] = Field(min_length=1)


class FilterDecision(FrozenModel):
    """Результат admission, где KEEP означает только отсутствие доказанного exclusion"""

    outcome: FilterOutcome
    exclusions: tuple[ProvenExclusion, ...] = ()

    @model_validator(mode="after")
    def validate_outcome(self) -> FilterDecision:
        if self.outcome is FilterOutcome.KEEP and self.exclusions:
            raise ValueError("KEEP decision must not carry exclusions")
        if self.outcome is FilterOutcome.EXCLUDE_PROVEN and not self.exclusions:
            raise ValueError("EXCLUDE_PROVEN decision requires at least one exclusion")
        return self
