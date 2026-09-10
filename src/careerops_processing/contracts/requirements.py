"""Контракты структурированных требований вакансии для P2-04"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .common import FrozenModel, NonEmptyStr, Sha256, VersionId
from .semantics import SemanticPolarity, SemanticSourceRef, SemanticSubject

REQUIREMENT_SET_SCHEMA_VERSION = "careerops.processing.requirement-set.v2"


class RequirementKind(StrEnum):
    TECHNOLOGY = "technology"
    EXPERIENCE = "experience"
    RESPONSIBILITY = "responsibility"
    EDUCATION = "education"
    LANGUAGE = "language"
    WORK_CONDITION = "work_condition"
    DOMAIN = "domain"
    OTHER = "other"


class RequirementImportance(StrEnum):
    MANDATORY = "mandatory"
    PREFERRED = "preferred"
    OPTIONAL = "optional"
    UNKNOWN = "unknown"


class RequirementModality(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    OPTIONAL = "optional"
    NOT_REQUIRED = "not_required"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"


class RequirementContext(StrEnum):
    QUALIFICATION = "qualification"
    RESPONSIBILITY = "responsibility"
    WORK_CONDITION = "work_condition"
    EDUCATION = "education"
    LANGUAGE = "language"
    UNKNOWN = "unknown"


class RequirementThresholdMetric(StrEnum):
    EXPERIENCE_YEARS = "experience_years"


class RequirementThreshold(FrozenModel):
    """Числовой порог требования без привязки к текстовому представлению"""

    metric: RequirementThresholdMetric
    minimum: Decimal | None = Field(default=None, ge=0)
    maximum: Decimal | None = Field(default=None, ge=0)
    unit: Literal["years"] = "years"

    @model_validator(mode="after")
    def validate_bounds(self) -> RequirementThreshold:
        if self.minimum is None and self.maximum is None:
            raise ValueError("requirement threshold requires minimum or maximum")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.maximum < self.minimum
        ):
            raise ValueError("requirement threshold maximum must be >= minimum")
        return self


class RequirementGroupOperator(StrEnum):
    ALL = "all"
    ANY = "any"
    CONDITIONAL = "conditional"


class Requirement(FrozenModel):
    """Одно атомарное требование с семантикой и точными ссылками на источник"""

    requirement_id: NonEmptyStr
    kind: RequirementKind
    statement: NonEmptyStr
    subjects: tuple[SemanticSubject, ...] = ()
    activity: NonEmptyStr | None = None
    context: RequirementContext = RequirementContext.UNKNOWN
    importance: RequirementImportance = RequirementImportance.UNKNOWN
    modality: RequirementModality = RequirementModality.UNKNOWN
    polarity: SemanticPolarity = SemanticPolarity.POSITIVE
    threshold: RequirementThreshold | None = None
    source_refs: tuple[SemanticSourceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_semantics(self) -> Requirement:
        expected_importance = {
            RequirementModality.REQUIRED: RequirementImportance.MANDATORY,
            RequirementModality.PREFERRED: RequirementImportance.PREFERRED,
            RequirementModality.OPTIONAL: RequirementImportance.OPTIONAL,
            RequirementModality.NOT_REQUIRED: RequirementImportance.OPTIONAL,
            RequirementModality.PROHIBITED: RequirementImportance.MANDATORY,
        }
        expected = expected_importance.get(self.modality)
        if expected is not None and self.importance is not expected:
            raise ValueError("requirement modality and importance are inconsistent")
        if self.modality is RequirementModality.PROHIBITED:
            if self.polarity is not SemanticPolarity.NEGATIVE:
                raise ValueError("PROHIBITED requirement must have NEGATIVE polarity")
        if self.modality is RequirementModality.NOT_REQUIRED:
            if self.polarity is not SemanticPolarity.POSITIVE:
                raise ValueError("NOT_REQUIRED requirement must have POSITIVE polarity")
        return self


class RequirementGroup(FrozenModel):
    """Однозначный логический узел дерева требований"""

    group_id: NonEmptyStr
    operator: RequirementGroupOperator
    requirement_ids: tuple[NonEmptyStr, ...] = ()
    child_group_ids: tuple[NonEmptyStr, ...] = ()
    condition: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_members(self) -> RequirementGroup:
        if len(self.requirement_ids) != len(set(self.requirement_ids)):
            raise ValueError("requirement group contains duplicate requirement ids")
        if len(self.child_group_ids) != len(set(self.child_group_ids)):
            raise ValueError("requirement group contains duplicate child group ids")

        member_count = len(self.requirement_ids) + len(self.child_group_ids)
        if member_count == 0:
            raise ValueError("requirement group must contain at least one member")

        if self.operator is RequirementGroupOperator.ANY:
            if member_count < 2:
                raise ValueError("ANY requirement group requires at least two members")
            if self.condition is not None:
                raise ValueError("ANY requirement group must not contain condition")
            return self

        if self.operator is RequirementGroupOperator.CONDITIONAL:
            if self.condition is None:
                raise ValueError("CONDITIONAL requirement group requires condition")
            if member_count != 1:
                raise ValueError("CONDITIONAL requirement group requires exactly one member")
            return self

        if self.condition is not None:
            raise ValueError("ALL requirement group must not contain condition")
        return self


class RequirementSet(FrozenModel):
    """Детерминированное дерево требований одной неизменяемой версии вакансии"""

    schema_version: VersionId = REQUIREMENT_SET_SCHEMA_VERSION
    source_key: NonEmptyStr
    source_entity_id: NonEmptyStr
    semantic_content_hash: Sha256
    normalized_schema_version: VersionId
    normalization_version: VersionId
    dictionary_version: VersionId
    extraction_version: VersionId
    requirements: tuple[Requirement, ...] = ()
    groups: tuple[RequirementGroup, ...] = ()
    root_group_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_graph(self) -> RequirementSet:
        requirement_ids = [item.requirement_id for item in self.requirements]
        group_ids = [item.group_id for item in self.groups]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirement ids must be unique within RequirementSet")
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("requirement group ids must be unique within RequirementSet")

        if not self.requirements:
            if self.groups or self.root_group_id is not None:
                raise ValueError("empty RequirementSet must not contain a requirement graph")
            return self

        group_by_id = {item.group_id: item for item in self.groups}
        requirement_id_set = set(requirement_ids)
        group_id_set = set(group_ids)
        if self.root_group_id is None:
            raise ValueError("non-empty RequirementSet requires root_group_id")
        if self.root_group_id not in group_by_id:
            raise ValueError("root_group_id does not reference an existing requirement group")
        if group_by_id[self.root_group_id].operator is not RequirementGroupOperator.ALL:
            raise ValueError("root requirement group must use ALL operator")

        requirement_owner_count = {item: 0 for item in requirement_ids}
        group_parent_count = {item: 0 for item in group_ids}
        for group in self.groups:
            unknown_requirements = set(group.requirement_ids) - requirement_id_set
            if unknown_requirements:
                raise ValueError("requirement group references unknown requirement ids")
            unknown_groups = set(group.child_group_ids) - group_id_set
            if unknown_groups:
                raise ValueError("requirement group references unknown child group ids")
            if group.group_id in group.child_group_ids:
                raise ValueError("requirement group cannot reference itself")
            for requirement_id in group.requirement_ids:
                requirement_owner_count[requirement_id] += 1
            for child_group_id in group.child_group_ids:
                group_parent_count[child_group_id] += 1

        if any(count != 1 for count in requirement_owner_count.values()):
            raise ValueError("every requirement must belong to exactly one requirement group")
        if group_parent_count[self.root_group_id] != 0:
            raise ValueError("root requirement group must not have a parent")
        for group_id, count in group_parent_count.items():
            if group_id == self.root_group_id:
                continue
            if count != 1:
                raise ValueError("every non-root requirement group must have exactly one parent")

        visited_groups: set[str] = set()
        active_groups: set[str] = set()
        reachable_requirements: set[str] = set()

        def walk(group_id: str) -> None:
            if group_id in active_groups:
                raise ValueError("requirement group graph must be acyclic")
            if group_id in visited_groups:
                return
            active_groups.add(group_id)
            group = group_by_id[group_id]
            reachable_requirements.update(group.requirement_ids)
            for child_group_id in group.child_group_ids:
                walk(child_group_id)
            active_groups.remove(group_id)
            visited_groups.add(group_id)

        walk(self.root_group_id)
        if reachable_requirements != requirement_id_set:
            raise ValueError("every requirement must be reachable from root_group_id")
        if visited_groups != group_id_set:
            raise ValueError("every requirement group must be reachable from root_group_id")
        return self
