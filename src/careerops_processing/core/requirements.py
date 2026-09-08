"""Детерминированное извлечение RequirementSet из NormalizedVacancy"""

from __future__ import annotations

import re
from decimal import Decimal

from careerops_processing.contracts.common import SourceLabel, TextBlock
from careerops_processing.contracts.normalized import NormalizedVacancy
from careerops_processing.contracts.requirements import (
    Requirement,
    RequirementGroup,
    RequirementGroupOperator,
    RequirementImportance,
    RequirementKind,
    RequirementModality,
    RequirementSet,
)
from careerops_processing.contracts.semantics import SemanticSourceRef, SemanticSubject

from .text_semantics import (
    detect_polarity,
    detect_requirement_signal,
    extract_minimum_years,
    normalize_space,
    normalize_subject,
    split_alternatives,
    split_condition,
    split_statements,
    stable_semantic_id,
    strip_requirement_markers,
    subjects_from_statement,
)

_EDUCATION = re.compile(
    r"\b(?:образовани|высшее|degree|bachelor|master|education)\w*\b",
    re.I,
)
_LANGUAGE = re.compile(
    r"\b(?:english|английск|русск|german|немецк|french|французск)\w*\b",
    re.I,
)
_WORK_CONDITION = re.compile(
    r"\b(?:remote|onsite|hybrid|удален|офис|гибрид|relocat|переезд)\w*\b",
    re.I,
)
_RESPONSIBILITY = re.compile(
    r"\b(?:разработ|поддерж|проектир|внедр|оптимиз|"
    r"develop|maintain|design|implement|build|operate)\w*\b",
    re.I,
)

_REQUIREMENT_SECTIONS = (
    "требован",
    "requirements",
    "qualification",
    "must have",
)
_RESPONSIBILITY_SECTIONS = (
    "обязанност",
    "задач",
    "responsibilit",
    "what you will do",
)


def _source_ref_for_block(block: TextBlock, statement: str) -> SemanticSourceRef:
    return SemanticSourceRef(
        source_path=f"text_blocks[{block.ordinal}]",
        rendered_value=statement,
        block_id=block.block_id,
        source_text=block.source_ref,
    )


def _source_ref_for_skill(skill: SourceLabel, index: int) -> SemanticSourceRef:
    return SemanticSourceRef(
        source_path=f"key_skills[{index}]",
        rendered_value=skill.label or skill.key,
    )


def _section_text(block: TextBlock) -> str:
    return " ".join(part for part in (block.heading, block.section_hint) if part).casefold()


def _classify_kind(
    statement: str,
    block: TextBlock | None,
    minimum_years: Decimal | None,
) -> RequirementKind:
    if minimum_years is not None:
        return RequirementKind.EXPERIENCE
    if _EDUCATION.search(statement):
        return RequirementKind.EDUCATION
    if _LANGUAGE.search(statement):
        return RequirementKind.LANGUAGE
    if _WORK_CONDITION.search(statement):
        return RequirementKind.WORK_CONDITION
    if block is not None:
        section = _section_text(block)
        if any(marker in section for marker in _RESPONSIBILITY_SECTIONS):
            return RequirementKind.RESPONSIBILITY
        if any(marker in section for marker in _REQUIREMENT_SECTIONS):
            cleaned = strip_requirement_markers(statement)
            if cleaned and len(cleaned) <= 80:
                return RequirementKind.TECHNOLOGY
    if _RESPONSIBILITY.search(statement):
        return RequirementKind.RESPONSIBILITY
    if strip_requirement_markers(statement) and len(strip_requirement_markers(statement)) <= 60:
        return RequirementKind.TECHNOLOGY
    return RequirementKind.OTHER


def _make_requirement(
    *,
    statement: str,
    source_ref: SemanticSourceRef,
    extraction_version: str,
    known_subjects: tuple[str, ...],
    block: TextBlock | None = None,
    forced_subject: str | None = None,
    minimum_years_override: Decimal | None = None,
    condition: str | None = None,
) -> Requirement:
    minimum_years = (
        extract_minimum_years(statement)
        if minimum_years_override is None
        else minimum_years_override
    )
    signal = detect_requirement_signal(
        statement,
        section_hint=block.section_hint if block is not None else None,
        heading=block.heading if block is not None else None,
    )
    subjects: tuple[SemanticSubject, ...]
    if forced_subject is None:
        subjects = subjects_from_statement(statement, known_subjects=known_subjects)
    else:
        cleaned = strip_requirement_markers(forced_subject)
        normalized = normalize_subject(cleaned)
        subjects = (SemanticSubject(text=cleaned, normalized=normalized),) if cleaned else ()
    kind = _classify_kind(statement, block, minimum_years)
    identity = {
        "statement": normalize_space(statement).casefold(),
        "subjects": [subject.normalized for subject in subjects],
        "kind": kind.value,
        "importance": signal.importance,
        "modality": signal.modality,
        "polarity": detect_polarity(statement).value,
        "minimum_experience_years": str(minimum_years) if minimum_years is not None else None,
        "condition": condition,
        "source_path": source_ref.source_path,
        "block_id": source_ref.block_id,
        "extraction_version": extraction_version,
    }
    return Requirement(
        requirement_id=stable_semantic_id("req", identity),
        kind=kind,
        statement=normalize_space(statement),
        subjects=subjects,
        importance=RequirementImportance(signal.importance),
        modality=RequirementModality(signal.modality),
        polarity=detect_polarity(statement),
        minimum_experience_years=minimum_years,
        condition=condition,
        source_refs=(source_ref,),
    )


def _skill_requirement(
    *,
    skill: SourceLabel,
    index: int,
    extraction_version: str,
) -> Requirement:
    rendered = skill.label or skill.key
    subject = SemanticSubject(
        text=rendered,
        normalized=normalize_subject(rendered),
        dictionary_key=skill.key,
    )
    source_ref = _source_ref_for_skill(skill, index)
    identity = {
        "subject": subject.normalized,
        "dictionary_key": skill.key,
        "source_path": source_ref.source_path,
        "extraction_version": extraction_version,
    }
    return Requirement(
        requirement_id=stable_semantic_id("req", identity),
        kind=RequirementKind.TECHNOLOGY,
        statement=rendered,
        subjects=(subject,),
        importance=RequirementImportance.UNKNOWN,
        modality=RequirementModality.UNKNOWN,
        source_refs=(source_ref,),
    )


def _deduplicate_requirements(items: list[Requirement]) -> list[Requirement]:
    by_id: dict[str, Requirement] = {}
    for item in items:
        existing = by_id.get(item.requirement_id)
        if existing is None:
            by_id[item.requirement_id] = item
            continue
        refs = tuple(dict.fromkeys((*existing.source_refs, *item.source_refs)))
        by_id[item.requirement_id] = existing.model_copy(update={"source_refs": refs})
    return list(by_id.values())


def extract_requirements(
    vacancy: NormalizedVacancy,
    *,
    extraction_version: str,
) -> RequirementSet:
    """Строит RequirementSet один раз на immutable vacancy version"""

    known_subjects = tuple(skill.label or skill.key for skill in vacancy.key_skills)
    requirements: list[Requirement] = []
    direct_requirement_ids: list[str] = []
    groups: list[RequirementGroup] = []
    root_child_group_ids: list[str] = []

    for index, skill in enumerate(vacancy.key_skills):
        item = _skill_requirement(
            skill=skill,
            index=index,
            extraction_version=extraction_version,
        )
        requirements.append(item)
        direct_requirement_ids.append(item.requirement_id)

    for block in sorted(vacancy.text_blocks, key=lambda item: item.ordinal):
        section = _section_text(block)
        split_compact_lists = any(marker in section for marker in _REQUIREMENT_SECTIONS)
        statements = split_statements(
            block.text,
            split_compact_lists=split_compact_lists,
        )
        for statement_index, statement in enumerate(statements):
            source_ref = _source_ref_for_block(block, statement)
            condition = split_condition(statement)
            condition_text = condition[0] if condition is not None else None
            statement_body = condition[1] if condition is not None else statement
            alternatives = split_alternatives(statement_body)

            if len(alternatives) > 1:
                alternative_ids: list[str] = []
                shared_years = extract_minimum_years(statement_body)
                for alternative in alternatives:
                    item = _make_requirement(
                        statement=alternative,
                        source_ref=source_ref,
                        extraction_version=extraction_version,
                        known_subjects=known_subjects,
                        block=block,
                        forced_subject=alternative,
                        minimum_years_override=shared_years,
                        condition=condition_text,
                    )
                    requirements.append(item)
                    alternative_ids.append(item.requirement_id)

                any_identity = {
                    "block_id": block.block_id,
                    "statement_index": statement_index,
                    "operator": RequirementGroupOperator.ANY.value,
                    "requirements": alternative_ids,
                    "extraction_version": extraction_version,
                }
                any_group = RequirementGroup(
                    group_id=stable_semantic_id("rgrp", any_identity),
                    operator=RequirementGroupOperator.ANY,
                    requirement_ids=tuple(dict.fromkeys(alternative_ids)),
                )
                groups.append(any_group)

                if condition_text is None:
                    root_child_group_ids.append(any_group.group_id)
                    continue

                conditional_identity = {
                    "block_id": block.block_id,
                    "statement_index": statement_index,
                    "operator": RequirementGroupOperator.CONDITIONAL.value,
                    "child_group": any_group.group_id,
                    "condition": condition_text,
                    "extraction_version": extraction_version,
                }
                conditional_group = RequirementGroup(
                    group_id=stable_semantic_id("rgrp", conditional_identity),
                    operator=RequirementGroupOperator.CONDITIONAL,
                    child_group_ids=(any_group.group_id,),
                    condition=condition_text,
                )
                groups.append(conditional_group)
                root_child_group_ids.append(conditional_group.group_id)
                continue

            item = _make_requirement(
                statement=statement_body,
                source_ref=source_ref,
                extraction_version=extraction_version,
                known_subjects=known_subjects,
                block=block,
                condition=condition_text,
            )
            requirements.append(item)
            if condition_text is None:
                direct_requirement_ids.append(item.requirement_id)
                continue

            conditional_identity = {
                "block_id": block.block_id,
                "statement_index": statement_index,
                "operator": RequirementGroupOperator.CONDITIONAL.value,
                "requirements": [item.requirement_id],
                "condition": condition_text,
                "extraction_version": extraction_version,
            }
            conditional_group = RequirementGroup(
                group_id=stable_semantic_id("rgrp", conditional_identity),
                operator=RequirementGroupOperator.CONDITIONAL,
                requirement_ids=(item.requirement_id,),
                condition=condition_text,
            )
            groups.append(conditional_group)
            root_child_group_ids.append(conditional_group.group_id)

    requirements = _deduplicate_requirements(requirements)
    requirement_ids = {item.requirement_id for item in requirements}
    direct_requirement_ids = list(
        dict.fromkeys(item for item in direct_requirement_ids if item in requirement_ids)
    )

    cleaned_groups: list[RequirementGroup] = []
    valid_group_ids = {group.group_id for group in groups}
    for group in groups:
        requirement_refs = tuple(
            item for item in group.requirement_ids if item in requirement_ids
        )
        child_refs = tuple(
            item for item in group.child_group_ids if item in valid_group_ids
        )
        if not requirement_refs and not child_refs:
            continue
        cleaned_groups.append(
            group.model_copy(
                update={
                    "requirement_ids": requirement_refs,
                    "child_group_ids": child_refs,
                }
            )
        )

    groups = cleaned_groups
    group_ids = {group.group_id for group in groups}
    root_child_group_ids = list(
        dict.fromkeys(item for item in root_child_group_ids if item in group_ids)
    )

    if not requirements:
        return RequirementSet(
            source_key=vacancy.source_key,
            source_entity_id=vacancy.source_entity_id,
            semantic_content_hash=vacancy.semantic_content_hash,
            normalized_schema_version=vacancy.schema_version,
            normalization_version=vacancy.normalization_version,
            dictionary_version=vacancy.dictionary_version,
            extraction_version=extraction_version,
        )

    root_identity = {
        "source_entity_id": vacancy.source_entity_id,
        "semantic_content_hash": vacancy.semantic_content_hash,
        "operator": RequirementGroupOperator.ALL.value,
        "requirements": direct_requirement_ids,
        "children": root_child_group_ids,
        "extraction_version": extraction_version,
    }
    root = RequirementGroup(
        group_id=stable_semantic_id("rgrp", root_identity),
        operator=RequirementGroupOperator.ALL,
        requirement_ids=tuple(direct_requirement_ids),
        child_group_ids=tuple(root_child_group_ids),
    )
    all_groups = (root, *groups)
    return RequirementSet(
        source_key=vacancy.source_key,
        source_entity_id=vacancy.source_entity_id,
        semantic_content_hash=vacancy.semantic_content_hash,
        normalized_schema_version=vacancy.schema_version,
        normalization_version=vacancy.normalization_version,
        dictionary_version=vacancy.dictionary_version,
        extraction_version=extraction_version,
        requirements=tuple(requirements),
        groups=all_groups,
        root_group_id=root.group_id,
    )
