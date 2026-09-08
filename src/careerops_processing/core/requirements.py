"""Детерминированное извлечение RequirementSet из NormalizedVacancy"""

from __future__ import annotations

import re
from decimal import Decimal

from careerops_processing.contracts.common import SourceLabel, TextBlock
from careerops_processing.contracts.normalized import NormalizedVacancy
from careerops_processing.contracts.requirements import (
    Requirement,
    RequirementContext,
    RequirementGroup,
    RequirementGroupOperator,
    RequirementImportance,
    RequirementKind,
    RequirementModality,
    RequirementSet,
    RequirementThreshold,
    RequirementThresholdMetric,
)
from careerops_processing.contracts.semantics import (
    SemanticPolarity,
    SemanticSourceRef,
    SemanticSubject,
)

from .text_semantics import (
    detect_activity,
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

_EDUCATION = re.compile(r"\b(?:образовани|высшее|degree|bachelor|master|education)\w*\b", re.I)
_LANGUAGE = re.compile(r"\b(?:english|английск|русск|german|немецк|french|французск)\w*\b", re.I)
_WORK_CONDITION = re.compile(
    r"\b(?:remote|onsite|hybrid|удален|офис|гибрид|relocat|переезд)\w*\b",
    re.I,
)
_RESPONSIBILITY = re.compile(
    r"\b(?:разработ|поддерж|проектир|внедр|оптимиз|develop|maintain|design|implement|build|operate)\w*\b",
    re.I,
)

_REQUIREMENT_SECTIONS = ("требован", "requirements", "qualification", "must have")
_RESPONSIBILITY_SECTIONS = ("обязанност", "задач", "responsibilit", "what you will do")
_GENERIC_EXPERIENCE_SUBJECTS = {"опыт", "experience", "стаж"}


def _source_ref_for_block(block: TextBlock, statement: str) -> SemanticSourceRef:
    return SemanticSourceRef(
        source_path=f"text_blocks[{block.ordinal}]",
        rendered_value=statement,
        block_id=block.block_id,
        source_text=block.source_ref,
    )


def _source_ref(path: str, rendered_value: str) -> SemanticSourceRef:
    return SemanticSourceRef(source_path=path, rendered_value=rendered_value)


def _section_text(block: TextBlock) -> str:
    return " ".join(part for part in (block.heading, block.section_hint) if part).casefold()


def _known_subjects(vacancy: NormalizedVacancy) -> tuple[str, ...]:
    values: list[str] = []
    for skill in vacancy.key_skills:
        values.append(skill.key)
        if skill.label is not None:
            values.append(skill.label)
    return tuple(dict.fromkeys(values))


def _has_specific_subject(subjects: tuple[SemanticSubject, ...]) -> bool:
    return any(subject.normalized not in _GENERIC_EXPERIENCE_SUBJECTS for subject in subjects)


def _classify_kind(
    statement: str,
    block: TextBlock | None,
    threshold: RequirementThreshold | None,
    subjects: tuple[SemanticSubject, ...],
) -> RequirementKind:
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
            if _has_specific_subject(subjects) and cleaned:
                return RequirementKind.TECHNOLOGY

    if _RESPONSIBILITY.search(statement):
        return RequirementKind.RESPONSIBILITY
    if _has_specific_subject(subjects):
        return RequirementKind.TECHNOLOGY
    if threshold is not None and threshold.metric is RequirementThresholdMetric.EXPERIENCE_YEARS:
        return RequirementKind.EXPERIENCE
    if strip_requirement_markers(statement) and len(strip_requirement_markers(statement)) <= 60:
        return RequirementKind.TECHNOLOGY
    return RequirementKind.OTHER


def _context_for(kind: RequirementKind, block: TextBlock | None) -> RequirementContext:
    if kind is RequirementKind.RESPONSIBILITY:
        return RequirementContext.RESPONSIBILITY
    if kind is RequirementKind.WORK_CONDITION:
        return RequirementContext.WORK_CONDITION
    if kind is RequirementKind.EDUCATION:
        return RequirementContext.EDUCATION
    if kind is RequirementKind.LANGUAGE:
        return RequirementContext.LANGUAGE
    if block is not None and any(
        marker in _section_text(block) for marker in _REQUIREMENT_SECTIONS
    ):
        return RequirementContext.QUALIFICATION
    return RequirementContext.UNKNOWN


def _years_threshold(
    minimum: Decimal | None,
    maximum: Decimal | None = None,
) -> RequirementThreshold | None:
    if minimum is None and maximum is None:
        return None
    return RequirementThreshold(
        metric=RequirementThresholdMetric.EXPERIENCE_YEARS,
        minimum=minimum,
        maximum=maximum,
    )


def _requirement_identity(
    *,
    kind: RequirementKind,
    subjects: tuple[SemanticSubject, ...],
    statement: str,
    activity: str | None,
    context: RequirementContext,
    importance: RequirementImportance,
    modality: RequirementModality,
    polarity: SemanticPolarity,
    threshold: RequirementThreshold | None,
    logical_scope: str,
    extraction_version: str,
) -> dict[str, object]:
    semantic_text = strip_requirement_markers(statement) or normalize_space(statement)
    return {
        "kind": kind.value,
        "subjects": [item.normalized for item in subjects],
        "semantic_text": normalize_subject(semantic_text),
        "activity": activity,
        "context": context.value,
        "importance": importance.value,
        "modality": modality.value,
        "polarity": polarity.value,
        "threshold": threshold.model_dump(mode="json") if threshold is not None else None,
        "logical_scope": logical_scope,
        "extraction_version": extraction_version,
    }


def _make_requirement(
    *,
    statement: str,
    source_ref: SemanticSourceRef,
    extraction_version: str,
    known_subjects: tuple[str, ...],
    logical_scope: str,
    block: TextBlock | None = None,
    forced_subject: str | None = None,
    threshold_override: RequirementThreshold | None = None,
) -> Requirement:
    threshold = threshold_override
    if threshold is None:
        threshold = _years_threshold(extract_minimum_years(statement))
    signal = detect_requirement_signal(
        statement,
        section_hint=block.section_hint if block is not None else None,
        heading=block.heading if block is not None else None,
    )
    if forced_subject is None:
        subjects = subjects_from_statement(statement, known_subjects=known_subjects)
    else:
        cleaned = strip_requirement_markers(forced_subject)
        normalized = normalize_subject(cleaned)
        subjects = (SemanticSubject(text=cleaned, normalized=normalized),) if normalized else ()
    kind = _classify_kind(statement, block, threshold, subjects)
    context = _context_for(kind, block)
    activity = detect_activity(statement)
    identity = _requirement_identity(
        kind=kind,
        subjects=subjects,
        statement=statement,
        activity=activity,
        context=context,
        importance=signal.importance,
        modality=signal.modality,
        polarity=signal.polarity,
        threshold=threshold,
        logical_scope=logical_scope,
        extraction_version=extraction_version,
    )
    return Requirement(
        requirement_id=stable_semantic_id("req", identity),
        kind=kind,
        statement=normalize_space(statement),
        subjects=subjects,
        activity=activity,
        context=context,
        importance=signal.importance,
        modality=signal.modality,
        polarity=signal.polarity,
        threshold=threshold,
        source_refs=(source_ref,),
    )


def _structured_label_requirement(
    *,
    label: SourceLabel,
    source_path: str,
    extraction_version: str,
) -> Requirement:
    rendered = label.label or label.key
    subject = SemanticSubject(
        text=rendered,
        normalized=normalize_subject(rendered),
        dictionary_key=label.key,
    )
    identity = _requirement_identity(
        kind=RequirementKind.WORK_CONDITION,
        subjects=(subject,),
        statement=rendered,
        activity=None,
        context=RequirementContext.WORK_CONDITION,
        importance=RequirementImportance.UNKNOWN,
        modality=RequirementModality.UNKNOWN,
        polarity=SemanticPolarity.POSITIVE,
        threshold=None,
        logical_scope="root",
        extraction_version=extraction_version,
    )
    return Requirement(
        requirement_id=stable_semantic_id("req", identity),
        kind=RequirementKind.WORK_CONDITION,
        statement=rendered,
        subjects=(subject,),
        context=RequirementContext.WORK_CONDITION,
        source_refs=(_source_ref(source_path, rendered),),
    )


def _structured_fact_requirement(
    *,
    value: str,
    source_path: str,
    extraction_version: str,
) -> Requirement:
    subject = SemanticSubject(text=value, normalized=normalize_subject(value))
    identity = _requirement_identity(
        kind=RequirementKind.WORK_CONDITION,
        subjects=(subject,),
        statement=value,
        activity=None,
        context=RequirementContext.WORK_CONDITION,
        importance=RequirementImportance.UNKNOWN,
        modality=RequirementModality.UNKNOWN,
        polarity=SemanticPolarity.POSITIVE,
        threshold=None,
        logical_scope="root",
        extraction_version=extraction_version,
    )
    return Requirement(
        requirement_id=stable_semantic_id("req", identity),
        kind=RequirementKind.WORK_CONDITION,
        statement=value,
        subjects=(subject,),
        context=RequirementContext.WORK_CONDITION,
        source_refs=(_source_ref(source_path, value),),
    )


def _structured_experience_requirement(
    vacancy: NormalizedVacancy,
    extraction_version: str,
) -> Requirement | None:
    value = vacancy.experience.value
    if value is None:
        return None

    threshold = _years_threshold(value.minimum_years)
    if threshold is None and value.source_code is None:
        return None

    if value.minimum_years is not None and value.maximum_years is not None:
        statement = f"Опыт {value.minimum_years}–{value.maximum_years} лет"
    elif value.minimum_years is not None:
        statement = f"Опыт от {value.minimum_years} лет"
    elif value.maximum_years is not None:
        statement = f"Категория опыта до {value.maximum_years} лет"
    else:
        assert value.source_code is not None
        statement = f"Категория опыта {value.source_code}"

    subject = SemanticSubject(text="опыт", normalized="опыт")
    importance = (
        RequirementImportance.MANDATORY
        if threshold is not None
        else RequirementImportance.UNKNOWN
    )
    modality = (
        RequirementModality.REQUIRED
        if threshold is not None
        else RequirementModality.UNKNOWN
    )
    identity = _requirement_identity(
        kind=RequirementKind.EXPERIENCE,
        subjects=(subject,),
        statement=statement,
        activity=None,
        context=RequirementContext.QUALIFICATION,
        importance=importance,
        modality=modality,
        polarity=SemanticPolarity.POSITIVE,
        threshold=threshold,
        logical_scope="root",
        extraction_version=extraction_version,
    )
    return Requirement(
        requirement_id=stable_semantic_id("req", identity),
        kind=RequirementKind.EXPERIENCE,
        statement=statement,
        subjects=(subject,),
        context=RequirementContext.QUALIFICATION,
        importance=importance,
        modality=modality,
        threshold=threshold,
        source_refs=(
            _source_ref(
                "experience",
                value.source_code or statement,
            ),
        ),
    )


def _merge_requirement(items: dict[str, Requirement], item: Requirement) -> None:
    existing = items.get(item.requirement_id)
    if existing is None:
        items[item.requirement_id] = item
        return
    refs = tuple(dict.fromkeys((*existing.source_refs, *item.source_refs)))
    items[item.requirement_id] = existing.model_copy(update={"source_refs": refs})


def _merge_group(items: dict[str, RequirementGroup], group: RequirementGroup) -> None:
    existing = items.get(group.group_id)
    if existing is None:
        items[group.group_id] = group
        return
    if existing != group:
        raise ValueError("одинаковый group_id описывает разные requirement groups")


def extract_requirements(
    vacancy: NormalizedVacancy,
    *,
    extraction_version: str,
) -> RequirementSet:
    """Строит RequirementSet один раз для одной semantic vacancy version"""

    known_subjects = _known_subjects(vacancy)
    requirements: dict[str, Requirement] = {}
    groups: dict[str, RequirementGroup] = {}
    root_requirement_ids: list[str] = []
    root_child_group_ids: list[str] = []

    for index, skill in enumerate(vacancy.key_skills):
        rendered = skill.label or skill.key
        subject = SemanticSubject(
            text=rendered,
            normalized=normalize_subject(rendered),
            dictionary_key=skill.key,
        )
        identity = _requirement_identity(
            kind=RequirementKind.TECHNOLOGY,
            subjects=(subject,),
            statement=rendered,
            activity=None,
            context=RequirementContext.QUALIFICATION,
            importance=RequirementImportance.UNKNOWN,
            modality=RequirementModality.UNKNOWN,
            polarity=SemanticPolarity.POSITIVE,
            threshold=None,
            logical_scope="root",
            extraction_version=extraction_version,
        )
        item = Requirement(
            requirement_id=stable_semantic_id("req", identity),
            kind=RequirementKind.TECHNOLOGY,
            statement=rendered,
            subjects=(subject,),
            context=RequirementContext.QUALIFICATION,
            source_refs=(_source_ref(f"key_skills[{index}]", rendered),),
        )
        _merge_requirement(requirements, item)
        root_requirement_ids.append(item.requirement_id)

    structured_experience = _structured_experience_requirement(vacancy, extraction_version)
    if structured_experience is not None:
        _merge_requirement(requirements, structured_experience)
        root_requirement_ids.append(structured_experience.requirement_id)

    for field_name, labels in (
        ("employment", vacancy.employment),
        ("schedules", vacancy.schedules),
        ("work_formats", vacancy.work_formats),
    ):
        for index, label in enumerate(labels):
            item = _structured_label_requirement(
                label=label,
                source_path=f"{field_name}[{index}]",
                extraction_version=extraction_version,
            )
            _merge_requirement(requirements, item)
            root_requirement_ids.append(item.requirement_id)

    if vacancy.location.value is not None:
        location = vacancy.location.value
        location_text = location.area_name or location.address
        if location_text is not None:
            item = _structured_fact_requirement(
                value=location_text,
                source_path="location",
                extraction_version=extraction_version,
            )
            _merge_requirement(requirements, item)
            root_requirement_ids.append(item.requirement_id)

    for index, fact in enumerate(vacancy.relocation_facts):
        item = _structured_fact_requirement(
            value=fact,
            source_path=f"relocation_facts[{index}]",
            extraction_version=extraction_version,
        )
        _merge_requirement(requirements, item)
        root_requirement_ids.append(item.requirement_id)

    for block in sorted(vacancy.text_blocks, key=lambda item: item.ordinal):
        section = _section_text(block)
        statements = split_statements(
            block.text,
            split_compact_lists=any(marker in section for marker in _REQUIREMENT_SECTIONS),
        )
        for statement in statements:
            source_ref = _source_ref_for_block(block, statement)
            condition = split_condition(statement)
            condition_text = condition[0] if condition is not None else None
            statement_body = condition[1] if condition is not None else statement
            alternatives = split_alternatives(statement_body)

            if len(alternatives) > 1:
                normalized_alternatives = [normalize_subject(item) for item in alternatives]
                logical_scope = stable_semantic_id(
                    "scope",
                    {
                        "operator": "any",
                        "alternatives": normalized_alternatives,
                        "condition": condition_text,
                        "extraction_version": extraction_version,
                    },
                )
                shared_threshold = _years_threshold(extract_minimum_years(statement_body))
                alternative_ids: list[str] = []
                for alternative in alternatives:
                    item = _make_requirement(
                        statement=alternative,
                        source_ref=source_ref,
                        extraction_version=extraction_version,
                        known_subjects=known_subjects,
                        logical_scope=logical_scope,
                        block=block,
                        forced_subject=alternative,
                        threshold_override=shared_threshold,
                    )
                    _merge_requirement(requirements, item)
                    alternative_ids.append(item.requirement_id)

                any_group = RequirementGroup(
                    group_id=stable_semantic_id(
                        "rgrp",
                        {
                            "operator": RequirementGroupOperator.ANY.value,
                            "requirements": list(dict.fromkeys(alternative_ids)),
                            "extraction_version": extraction_version,
                        },
                    ),
                    operator=RequirementGroupOperator.ANY,
                    requirement_ids=tuple(dict.fromkeys(alternative_ids)),
                )
                _merge_group(groups, any_group)
                if condition_text is None:
                    root_child_group_ids.append(any_group.group_id)
                    continue

                conditional_group = RequirementGroup(
                    group_id=stable_semantic_id(
                        "rgrp",
                        {
                            "operator": RequirementGroupOperator.CONDITIONAL.value,
                            "child": any_group.group_id,
                            "condition": normalize_subject(condition_text),
                            "extraction_version": extraction_version,
                        },
                    ),
                    operator=RequirementGroupOperator.CONDITIONAL,
                    child_group_ids=(any_group.group_id,),
                    condition=condition_text,
                )
                _merge_group(groups, conditional_group)
                root_child_group_ids.append(conditional_group.group_id)
                continue

            logical_scope = (
                "root"
                if condition_text is None
                else stable_semantic_id(
                    "scope",
                    {
                        "operator": "conditional",
                        "condition": normalize_subject(condition_text),
                        "body": normalize_subject(statement_body),
                        "extraction_version": extraction_version,
                    },
                )
            )
            item = _make_requirement(
                statement=statement_body,
                source_ref=source_ref,
                extraction_version=extraction_version,
                known_subjects=known_subjects,
                logical_scope=logical_scope,
                block=block,
            )
            _merge_requirement(requirements, item)
            if condition_text is None:
                root_requirement_ids.append(item.requirement_id)
                continue

            conditional_group = RequirementGroup(
                group_id=stable_semantic_id(
                    "rgrp",
                    {
                        "operator": RequirementGroupOperator.CONDITIONAL.value,
                        "requirement": item.requirement_id,
                        "condition": normalize_subject(condition_text),
                        "extraction_version": extraction_version,
                    },
                ),
                operator=RequirementGroupOperator.CONDITIONAL,
                requirement_ids=(item.requirement_id,),
                condition=condition_text,
            )
            _merge_group(groups, conditional_group)
            root_child_group_ids.append(conditional_group.group_id)

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

    root_requirement_ids = list(dict.fromkeys(root_requirement_ids))
    root_child_group_ids = list(dict.fromkeys(root_child_group_ids))
    child_owned_requirements = {
        requirement_id
        for group in groups.values()
        for requirement_id in group.requirement_ids
    }
    root_requirement_ids = [
        item for item in root_requirement_ids if item not in child_owned_requirements
    ]

    root = RequirementGroup(
        group_id=stable_semantic_id(
            "rgrp",
            {
                "source_entity_id": vacancy.source_entity_id,
                "semantic_content_hash": vacancy.semantic_content_hash,
                "operator": RequirementGroupOperator.ALL.value,
                "requirements": root_requirement_ids,
                "children": root_child_group_ids,
                "extraction_version": extraction_version,
            },
        ),
        operator=RequirementGroupOperator.ALL,
        requirement_ids=tuple(root_requirement_ids),
        child_group_ids=tuple(root_child_group_ids),
    )

    ordered_groups = (root, *groups.values())
    return RequirementSet(
        source_key=vacancy.source_key,
        source_entity_id=vacancy.source_entity_id,
        semantic_content_hash=vacancy.semantic_content_hash,
        normalized_schema_version=vacancy.schema_version,
        normalization_version=vacancy.normalization_version,
        dictionary_version=vacancy.dictionary_version,
        extraction_version=extraction_version,
        requirements=tuple(requirements.values()),
        groups=ordered_groups,
        root_group_id=root.group_id,
    )
