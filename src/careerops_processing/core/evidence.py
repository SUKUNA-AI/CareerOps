"""Детерминированное извлечение ResumeEvidenceSet из NormalizedResume"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from careerops_processing.contracts.common import SourceLabel, SourceValue, TextBlock, ValueState
from careerops_processing.contracts.evidence import (
    EvidenceActorScope,
    EvidenceContext,
    EvidenceKind,
    EvidenceStrength,
    ResumeEvidence,
    ResumeEvidenceSet,
)
from careerops_processing.contracts.normalized import (
    EducationEntry,
    ExperienceEntry,
    LanguageEntry,
    NormalizedResume,
    ProjectEntry,
    WorkPreferences,
)
from careerops_processing.contracts.semantics import (
    SemanticPolarity,
    SemanticSourceRef,
    SemanticSubject,
    SemanticTimeSpan,
)

from .text_semantics import (
    detect_activity,
    detect_polarity,
    detect_team_scope,
    normalize_space,
    normalize_subject,
    split_statements,
    stable_semantic_id,
    subjects_from_statement,
)


def _field_ref(
    path: str,
    rendered_value: str,
    *,
    entry_id: str | None = None,
) -> SemanticSourceRef:
    return SemanticSourceRef(
        source_path=path,
        rendered_value=rendered_value,
        entry_id=entry_id,
    )


def _block_ref(
    path: str,
    block: TextBlock,
    statement: str,
    *,
    entry_id: str,
) -> SemanticSourceRef:
    return SemanticSourceRef(
        source_path=path,
        rendered_value=statement,
        entry_id=entry_id,
        block_id=block.block_id,
        source_text=block.source_ref,
    )


def _time_span(
    *,
    start_date: SourceValue[date],
    end_date: SourceValue[date],
    currently_active: SourceValue[bool] | None = None,
) -> SemanticTimeSpan | None:
    active: bool | None = None
    if currently_active is not None and currently_active.state is ValueState.KNOWN:
        active = currently_active.value
    if start_date.value is None and end_date.value is None and active is None:
        return None
    return SemanticTimeSpan(
        start_date=start_date.value,
        end_date=end_date.value,
        currently_active=active,
    )


def _evidence_identity(
    *,
    kind: EvidenceKind,
    statement: str,
    subjects: tuple[SemanticSubject, ...],
    activity: str | None,
    actor_scope: EvidenceActorScope,
    context: EvidenceContext,
    polarity: SemanticPolarity,
    strength: EvidenceStrength,
    time_span: SemanticTimeSpan | None,
    evidence_version: str,
) -> dict[str, object]:
    return {
        "kind": kind.value,
        "subjects": [subject.normalized for subject in subjects],
        "semantic_text": None if subjects else normalize_subject(statement),
        "activity": activity,
        "actor_scope": actor_scope.value,
        "context": context.value,
        "polarity": polarity.value,
        "strength": strength.value,
        "time_span": time_span.model_dump(mode="json") if time_span is not None else None,
        "evidence_version": evidence_version,
    }


def _make_evidence(
    *,
    kind: EvidenceKind,
    statement: str,
    source_ref: SemanticSourceRef,
    evidence_version: str,
    context: EvidenceContext,
    actor_scope: EvidenceActorScope,
    strength: EvidenceStrength,
    known_subjects: tuple[str, ...],
    time_span: SemanticTimeSpan | None = None,
    explicit_subject: SemanticSubject | None = None,
    polarity_override: SemanticPolarity | None = None,
) -> ResumeEvidence:
    statement = normalize_space(statement)
    subjects = (
        (explicit_subject,)
        if explicit_subject is not None
        else subjects_from_statement(statement, known_subjects=known_subjects)
    )
    activity = detect_activity(statement)
    polarity = polarity_override or detect_polarity(statement)
    identity = _evidence_identity(
        kind=kind,
        statement=statement,
        subjects=subjects,
        activity=activity,
        actor_scope=actor_scope,
        context=context,
        polarity=polarity,
        strength=strength,
        time_span=time_span,
        evidence_version=evidence_version,
    )
    return ResumeEvidence(
        evidence_id=stable_semantic_id("ev", identity),
        kind=kind,
        statement=statement,
        subjects=subjects,
        activity=activity,
        actor_scope=actor_scope,
        context=context,
        polarity=polarity,
        strength=strength,
        time_span=time_span,
        source_refs=(source_ref,),
    )


def _known_subjects(resume: NormalizedResume) -> tuple[str, ...]:
    values: list[str] = []
    for skill in resume.skill_set:
        values.append(skill.key)
        if skill.label is not None:
            values.append(skill.label)
    return tuple(dict.fromkeys(values))


def _skill_evidence(skill: SourceLabel, index: int, evidence_version: str) -> ResumeEvidence:
    rendered = skill.label or skill.key
    subject = SemanticSubject(
        text=rendered,
        normalized=normalize_subject(rendered),
        dictionary_key=skill.key,
    )
    return _make_evidence(
        kind=EvidenceKind.SKILL,
        statement=rendered,
        source_ref=_field_ref(f"skill_set[{index}]", rendered),
        evidence_version=evidence_version,
        context=EvidenceContext.SKILL_LIST,
        actor_scope=EvidenceActorScope.SELF,
        strength=EvidenceStrength.MENTION,
        known_subjects=(rendered,),
        explicit_subject=subject,
    )


def _text_evidence(
    *,
    kind: EvidenceKind,
    context: EvidenceContext,
    blocks: tuple[TextBlock, ...],
    base_path: str,
    entry_id: str,
    evidence_version: str,
    known_subjects: tuple[str, ...],
    time_span: SemanticTimeSpan | None,
) -> list[ResumeEvidence]:
    result: list[ResumeEvidence] = []
    for block in sorted(blocks, key=lambda item: item.ordinal):
        for statement in split_statements(block.text):
            actor_scope = (
                EvidenceActorScope.TEAM
                if detect_team_scope(statement)
                else EvidenceActorScope.SELF
            )
            result.append(
                _make_evidence(
                    kind=kind,
                    statement=statement,
                    source_ref=_block_ref(
                        f"{base_path}.description_blocks[{block.ordinal}]",
                        block,
                        statement,
                        entry_id=entry_id,
                    ),
                    evidence_version=evidence_version,
                    context=context,
                    actor_scope=actor_scope,
                    strength=EvidenceStrength.DIRECT,
                    known_subjects=known_subjects,
                    time_span=time_span,
                )
            )
    return result


def _experience_evidence(
    entry: ExperienceEntry,
    *,
    index: int,
    evidence_version: str,
    known_subjects: tuple[str, ...],
) -> list[ResumeEvidence]:
    result: list[ResumeEvidence] = []
    span = _time_span(
        start_date=entry.start_date,
        end_date=entry.end_date,
        currently_active=entry.currently_active,
    )
    if entry.position.value is not None:
        result.append(
            _make_evidence(
                kind=EvidenceKind.EXPERIENCE,
                statement=entry.position.value,
                source_ref=_field_ref(
                    f"experience_entries[{index}].position",
                    entry.position.value,
                    entry_id=entry.entry_id,
                ),
                evidence_version=evidence_version,
                context=EvidenceContext.COMMERCIAL,
                actor_scope=EvidenceActorScope.SELF,
                strength=EvidenceStrength.SUPPORTED,
                known_subjects=known_subjects,
                time_span=span,
            )
        )
    result.extend(
        _text_evidence(
            kind=EvidenceKind.EXPERIENCE,
            context=EvidenceContext.COMMERCIAL,
            blocks=entry.description_blocks,
            base_path=f"experience_entries[{index}]",
            entry_id=entry.entry_id,
            evidence_version=evidence_version,
            known_subjects=known_subjects,
            time_span=span,
        )
    )
    return result


def _project_evidence(
    entry: ProjectEntry,
    *,
    index: int,
    evidence_version: str,
    known_subjects: tuple[str, ...],
) -> list[ResumeEvidence]:
    result: list[ResumeEvidence] = []
    span = _time_span(start_date=entry.start_date, end_date=entry.end_date)
    for field_name, value in (("name", entry.name.value), ("role", entry.role.value)):
        if value is None:
            continue
        result.append(
            _make_evidence(
                kind=EvidenceKind.PROJECT,
                statement=value,
                source_ref=_field_ref(
                    f"projects[{index}].{field_name}",
                    value,
                    entry_id=entry.project_id,
                ),
                evidence_version=evidence_version,
                context=EvidenceContext.PROJECT,
                actor_scope=EvidenceActorScope.PROJECT,
                strength=EvidenceStrength.SUPPORTED,
                known_subjects=known_subjects,
                time_span=span,
            )
        )
    result.extend(
        _text_evidence(
            kind=EvidenceKind.PROJECT,
            context=EvidenceContext.PROJECT,
            blocks=entry.description_blocks,
            base_path=f"projects[{index}]",
            entry_id=entry.project_id,
            evidence_version=evidence_version,
            known_subjects=known_subjects,
            time_span=span,
        )
    )
    return result


def _education_evidence(
    entry: EducationEntry,
    *,
    index: int,
    evidence_version: str,
) -> list[ResumeEvidence]:
    parts = [
        value
        for value in (entry.degree.value, entry.field.value, entry.organization.value)
        if value is not None
    ]
    if not parts:
        return []
    statement = " | ".join(parts)
    subject_text = entry.field.value or entry.degree.value or entry.organization.value
    assert subject_text is not None
    subject = SemanticSubject(text=subject_text, normalized=normalize_subject(subject_text))
    return [
        _make_evidence(
            kind=EvidenceKind.EDUCATION,
            statement=statement,
            source_ref=_field_ref(
                f"education[{index}]",
                statement,
                entry_id=entry.entry_id,
            ),
            evidence_version=evidence_version,
            context=EvidenceContext.EDUCATION,
            actor_scope=EvidenceActorScope.SELF,
            strength=EvidenceStrength.SUPPORTED,
            known_subjects=(),
            explicit_subject=subject,
        )
    ]


def _language_evidence(
    entry: LanguageEntry,
    *,
    index: int,
    evidence_version: str,
) -> ResumeEvidence:
    level = entry.level.value
    statement = entry.language if level is None else f"{entry.language}: {level}"
    subject = SemanticSubject(text=entry.language, normalized=normalize_subject(entry.language))
    return _make_evidence(
        kind=EvidenceKind.LANGUAGE,
        statement=statement,
        source_ref=_field_ref(f"languages[{index}]", statement),
        evidence_version=evidence_version,
        context=EvidenceContext.LANGUAGE,
        actor_scope=EvidenceActorScope.SELF,
        strength=EvidenceStrength.SUPPORTED if level is not None else EvidenceStrength.MENTION,
        known_subjects=(),
        explicit_subject=subject,
    )


def _preference_evidence(
    preferences: WorkPreferences,
    *,
    evidence_version: str,
) -> list[ResumeEvidence]:
    result: list[ResumeEvidence] = []
    values = (
        ("locations", preferences.locations),
        ("work_formats", preferences.work_formats),
        ("employment", preferences.employment),
        ("schedules", preferences.schedules),
    )
    for field_name, items in values:
        for index, item in enumerate(items):
            subject = SemanticSubject(text=item, normalized=normalize_subject(item))
            result.append(
                _make_evidence(
                    kind=EvidenceKind.PREFERENCE,
                    statement=item,
                    source_ref=_field_ref(f"work_preferences.{field_name}[{index}]", item),
                    evidence_version=evidence_version,
                    context=EvidenceContext.PREFERENCE,
                    actor_scope=EvidenceActorScope.SELF,
                    strength=EvidenceStrength.DIRECT,
                    known_subjects=(),
                    explicit_subject=subject,
                )
            )
    return result


def _boolean_preference_evidence(
    *,
    value: bool,
    source_path: str,
    subject_name: str,
    positive_statement: str,
    negative_statement: str,
    evidence_version: str,
) -> ResumeEvidence:
    statement = positive_statement if value else negative_statement
    subject = SemanticSubject(text=subject_name, normalized=normalize_subject(subject_name))
    return _make_evidence(
        kind=EvidenceKind.PREFERENCE,
        statement=statement,
        source_ref=_field_ref(source_path, statement),
        evidence_version=evidence_version,
        context=EvidenceContext.PREFERENCE,
        actor_scope=EvidenceActorScope.SELF,
        strength=EvidenceStrength.DIRECT,
        known_subjects=(),
        explicit_subject=subject,
        polarity_override=(SemanticPolarity.POSITIVE if value else SemanticPolarity.NEGATIVE),
    )


def _total_experience_evidence(
    years: Decimal,
    *,
    evidence_version: str,
) -> ResumeEvidence:
    statement = f"total_experience_years={years}"
    subject = SemanticSubject(text="total_experience", normalized="total_experience")
    return _make_evidence(
        kind=EvidenceKind.EXPERIENCE_SUMMARY,
        statement=statement,
        source_ref=_field_ref("total_experience_years", statement),
        evidence_version=evidence_version,
        context=EvidenceContext.COMMERCIAL,
        actor_scope=EvidenceActorScope.SELF,
        strength=EvidenceStrength.SUPPORTED,
        known_subjects=(),
        explicit_subject=subject,
    )


def _merge_evidence(items: dict[str, ResumeEvidence], item: ResumeEvidence) -> None:
    existing = items.get(item.evidence_id)
    if existing is None:
        items[item.evidence_id] = item
        return
    refs = tuple(dict.fromkeys((*existing.source_refs, *item.source_refs)))
    items[item.evidence_id] = existing.model_copy(update={"source_refs": refs})


def extract_resume_evidence(
    resume: NormalizedResume,
    *,
    evidence_version: str,
) -> ResumeEvidenceSet:
    """Строит ResumeEvidenceSet один раз для одной semantic resume version"""

    known_subjects = _known_subjects(resume)
    evidence: dict[str, ResumeEvidence] = {}

    if resume.headline.value is not None:
        headline = resume.headline.value
        subject = SemanticSubject(text=headline, normalized=normalize_subject(headline))
        _merge_evidence(
            evidence,
            _make_evidence(
                kind=EvidenceKind.HEADLINE,
                statement=headline,
                source_ref=_field_ref("headline", headline),
                evidence_version=evidence_version,
                context=EvidenceContext.SUMMARY,
                actor_scope=EvidenceActorScope.SELF,
                strength=EvidenceStrength.MENTION,
                known_subjects=(),
                explicit_subject=subject,
            ),
        )

    for index, skill in enumerate(resume.skill_set):
        _merge_evidence(evidence, _skill_evidence(skill, index, evidence_version))

    if resume.about.value is not None:
        for index, statement in enumerate(split_statements(resume.about.value)):
            _merge_evidence(
                evidence,
                _make_evidence(
                    kind=EvidenceKind.SUMMARY,
                    statement=statement,
                    source_ref=_field_ref(f"about[{index}]", statement),
                    evidence_version=evidence_version,
                    context=EvidenceContext.SUMMARY,
                    actor_scope=(
                        EvidenceActorScope.TEAM
                        if detect_team_scope(statement)
                        else EvidenceActorScope.SELF
                    ),
                    strength=EvidenceStrength.DIRECT,
                    known_subjects=known_subjects,
                ),
            )

    for index, experience_entry in enumerate(resume.experience_entries):
        for item in _experience_evidence(
            experience_entry,
            index=index,
            evidence_version=evidence_version,
            known_subjects=known_subjects,
        ):
            _merge_evidence(evidence, item)

    for index, project_entry in enumerate(resume.projects):
        for item in _project_evidence(
            project_entry,
            index=index,
            evidence_version=evidence_version,
            known_subjects=known_subjects,
        ):
            _merge_evidence(evidence, item)

    for index, education_entry in enumerate(resume.education):
        for item in _education_evidence(
            education_entry,
            index=index,
            evidence_version=evidence_version,
        ):
            _merge_evidence(evidence, item)

    for index, language_entry in enumerate(resume.languages):
        _merge_evidence(
            evidence,
            _language_evidence(
                language_entry,
                index=index,
                evidence_version=evidence_version,
            ),
        )

    if resume.location.value is not None:
        location = resume.location.value
        location_text = location.area_name or location.address
        if location_text is not None:
            subject = SemanticSubject(
                text=location_text,
                normalized=normalize_subject(location_text),
            )
            _merge_evidence(
                evidence,
                _make_evidence(
                    kind=EvidenceKind.LOCATION,
                    statement=location_text,
                    source_ref=_field_ref("location", location_text),
                    evidence_version=evidence_version,
                    context=EvidenceContext.CURRENT_LOCATION,
                    actor_scope=EvidenceActorScope.SELF,
                    strength=EvidenceStrength.DIRECT,
                    known_subjects=(),
                    explicit_subject=subject,
                ),
            )

    if resume.work_preferences.value is not None:
        for item in _preference_evidence(
            resume.work_preferences.value,
            evidence_version=evidence_version,
        ):
            _merge_evidence(evidence, item)

    if resume.relocation.value is not None:
        _merge_evidence(
            evidence,
            _boolean_preference_evidence(
                value=resume.relocation.value,
                source_path="relocation",
                subject_name="relocation",
                positive_statement="Готов к переезду",
                negative_statement="Не готов к переезду",
                evidence_version=evidence_version,
            ),
        )

    if resume.business_trips.value is not None:
        _merge_evidence(
            evidence,
            _boolean_preference_evidence(
                value=resume.business_trips.value,
                source_path="business_trips",
                subject_name="business_trips",
                positive_statement="Готов к командировкам",
                negative_statement="Не готов к командировкам",
                evidence_version=evidence_version,
            ),
        )

    if resume.total_experience_years.value is not None:
        _merge_evidence(
            evidence,
            _total_experience_evidence(
                resume.total_experience_years.value,
                evidence_version=evidence_version,
            ),
        )

    return ResumeEvidenceSet(
        source_key=resume.source_key,
        account_key=resume.account_key,
        source_entity_id=resume.source_entity_id,
        semantic_content_hash=resume.semantic_content_hash,
        normalized_schema_version=resume.schema_version,
        normalization_version=resume.normalization_version,
        dictionary_version=resume.dictionary_version,
        evidence_version=evidence_version,
        evidence=tuple(evidence.values()),
    )
