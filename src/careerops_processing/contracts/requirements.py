"""Контракты требований вакансии для P2-04"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from .common import FrozenModel, NonEmptyStr, Sha256, VersionId
from .semantics import SemanticPolarity, SemanticSourceRef, SemanticSubject

REQUIREMENT_SET_SCHEMA_VERSION = "careerops.processing.requirement-set.v1"


class RequirementKind(StrEnum):
    TECHNOLOGY = "technology"
    EXPERIENCE = "experience"
    RESPONSIBILITY = "responsibility"
    EDUCATION = "education"
    LANGUAGE = "language"
    WORK_CONDITION = "work_condition"
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
    UNKNOWN = "unknown"


class RequirementGroupOperator(StrEnum):
    ALL = "all"
    ANY = "any"
    CONDITIONAL = "conditional"


class Requirement(FrozenModel):
    """Одна атомарная requirement semantics с точным provenance"""

    requirement_id: NonEmptyStr
    kind: RequirementKind
    statement: NonEmptyStr
    subjects: tuple[SemanticSubject, ...] = ()
    importance: RequirementImportance = RequirementImportance.UNKNOWN
    modality: RequirementModality = RequirementModality.UNKNOWN
    polarity: SemanticPolarity = SemanticPolarity.POSITIVE
    minimum_experience_years: Decimal | None = Field(default=None, ge=0)
    condition: NonEmptyStr | None = None
    source_refs: tuple[SemanticSourceRef, ...] = Field(min_length=1)


class RequirementGroup(FrozenModel):
    """Логическая группа requirement ids и вложенных групп"""

    group_id: NonEmptyStr
    operator: RequirementGroupOperator
    requirement_ids: tuple[NonEmptyStr, ...] = ()
    child_group_ids: tuple[NonEmptyStr, ...] = ()
    condition: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_members(self) -> RequirementGroup:
        if not self.requirement_ids and not self.child_group_ids:
            raise ValueError("requirement group must contain at least one member")
        if len(self.requirement_ids) != len(set(self.requirement_ids)):
            raise ValueError("requirement group contains duplicate requirement ids")
        if len(self.child_group_ids) != len(set(self.child_group_ids)):
            raise ValueError("requirement group contains duplicate child group ids")
        return self


class RequirementSet(FrozenModel):
    """Детерминированная requirement representation одной vacancy version"""

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
    unresolved_fragments: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def validate_graph(self) -> RequirementSet:
        requirement_ids = [item.requirement_id for item in self.requirements]
        group_ids = [item.group_id for item in self.groups]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirement ids must be unique within RequirementSet")
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("requirement group ids must be unique within RequirementSet")

        requirement_id_set = set(requirement_ids)
        group_by_id = {item.group_id: item for item in self.groups}
        if not self.requirements:
            if self.groups or self.root_group_id is not None:
                raise ValueError("empty RequirementSet must not contain a requirement graph")
            return self
        if self.root_group_id is None:
            raise ValueError("non-empty RequirementSet requires root_group_id")
        if self.root_group_id not in group_by_id:
            raise ValueError("root_group_id does not reference an existing requirement group")

        for group in self.groups:
            unknown_requirements = set(group.requirement_ids) - requirement_id_set
            if unknown_requirements:
                raise ValueError("requirement group references unknown requirement ids")
            unknown_groups = set(group.child_group_ids) - set(group_ids)
            if unknown_groups:
                raise ValueError("requirement group references unknown child group ids")
            if group.group_id in group.child_group_ids:
                raise ValueError("requirement group cannot reference itself")

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
        if visited_groups != set(group_ids):
            raise ValueError("every requirement group must be reachable from root_group_id")
        return self
