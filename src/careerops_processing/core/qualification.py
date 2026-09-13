"""Pure deterministic P2-06 requirement qualification."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from careerops_processing.contracts.evidence import (
    EvidenceActorScope,
    EvidenceContext,
    EvidenceStrength,
    ResumeEvidence,
    ResumeEvidenceSet,
)
from careerops_processing.contracts.qualification import (
    RequirementGroupQualification,
    RequirementQualification,
    RequirementQualificationSet,
    RequirementQualificationState,
    SupportBounds,
)
from careerops_processing.contracts.requirements import (
    Requirement,
    RequirementGroup,
    RequirementGroupOperator,
    RequirementKind,
    RequirementModality,
    RequirementSet,
    RequirementThresholdMetric,
)
from careerops_processing.contracts.reranking import (
    EvidenceCandidateSet,
    RequirementEvidenceCandidates,
    RequirementSelectionState,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_DAYS_PER_YEAR = Decimal("365.2425")


def _bounds(lower: Decimal, upper: Decimal) -> SupportBounds:
    return SupportBounds(lower=lower, upper=upper)


def _subject_keys(requirement: Requirement | ResumeEvidence) -> frozenset[str]:
    values: set[str] = set()
    for subject in requirement.subjects:
        values.add(subject.dictionary_key or subject.normalized)
    return frozenset(values)


def _aligned(requirement: Requirement, evidence: ResumeEvidence) -> bool:
    requirement_subjects = _subject_keys(requirement)
    evidence_subjects = _subject_keys(evidence)
    if requirement_subjects:
        return bool(requirement_subjects & evidence_subjects)
    if requirement.activity is not None and evidence.activity is not None:
        return requirement.activity.casefold() == evidence.activity.casefold()
    return False


def _actor_is_decisive(requirement: Requirement, evidence: ResumeEvidence) -> bool:
    if evidence.actor_scope is EvidenceActorScope.SELF:
        return True
    return (
        evidence.actor_scope is EvidenceActorScope.PROJECT
        and evidence.context is EvidenceContext.PROJECT
        and requirement.kind in {RequirementKind.TECHNOLOGY, RequirementKind.RESPONSIBILITY}
    )


def _strength_is_decisive(evidence: ResumeEvidence) -> bool:
    return evidence.strength in {EvidenceStrength.DIRECT, EvidenceStrength.SUPPORTED}


def _duration_years(
    evidence: tuple[ResumeEvidence, ...],
    *,
    as_of: date,
) -> tuple[Decimal, bool]:
    intervals: list[tuple[date, date]] = []
    incomplete = False
    for item in evidence:
        span = item.time_span
        if span is None or span.start_date is None:
            incomplete = True
            continue
        end = span.end_date
        if end is None and span.currently_active is True:
            end = as_of
        if end is None:
            incomplete = True
            continue
        if end < span.start_date:
            incomplete = True
            continue
        intervals.append((span.start_date, end))

    if not intervals:
        return _ZERO, incomplete

    intervals.sort()
    merged: list[tuple[date, date]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        if end > previous_end:
            merged[-1] = (previous_start, end)

    total_days = sum((end - start).days for start, end in merged)
    return Decimal(total_days) / _DAYS_PER_YEAR, incomplete


def _selection_complete(
    selection: RequirementEvidenceCandidates,
    evidence_set: ResumeEvidenceSet,
) -> bool:
    """Return True only when P2-06 can prove it inspected the complete evidence corpus."""

    all_ids = tuple(item.evidence_id for item in evidence_set.evidence)
    if not all_ids:
        return selection.state is RequirementSelectionState.NO_EVIDENCE
    if selection.state is not RequirementSelectionState.RANKED:
        return False

    pool_ids = set(selection.pool_evidence_ids)
    evidence_ids = set(all_ids)
    if len(selection.pool_evidence_ids) != len(all_ids) or pool_ids != evidence_ids:
        return False

    selected_ids = {item.evidence_id for item in selection.candidates}
    return len(selection.candidates) == len(all_ids) and selected_ids == evidence_ids


def _selected_evidence(
    selection: RequirementEvidenceCandidates,
    evidence_by_id: dict[str, ResumeEvidence],
) -> tuple[ResumeEvidence, ...]:
    if any(evidence_id not in evidence_by_id for evidence_id in selection.pool_evidence_ids):
        raise ValueError("P2-05 candidate pool references unknown ResumeEvidence ids")
    if any(item.evidence_id not in evidence_by_id for item in selection.candidates):
        raise ValueError("P2-05 selected candidates reference unknown ResumeEvidence ids")
    return tuple(evidence_by_id[item.evidence_id] for item in selection.candidates)


def _qualify_one(
    requirement: Requirement,
    selection: RequirementEvidenceCandidates,
    evidence_set: ResumeEvidenceSet,
    *,
    as_of: date,
) -> RequirementQualification:
    evidence_by_id = {item.evidence_id: item for item in evidence_set.evidence}
    ranked_ids = tuple(item.evidence_id for item in selection.candidates)
    complete = _selection_complete(selection, evidence_set)

    if selection.state is RequirementSelectionState.NO_EVIDENCE:
        if evidence_set.evidence:
            raise ValueError("NO_EVIDENCE selection is inconsistent with non-empty evidence corpus")
        return RequirementQualification(
            requirement_id=requirement.requirement_id,
            state=RequirementQualificationState.NOT_EVIDENCED,
            support=_bounds(_ZERO, _ONE),
            selection_complete=True,
            reason_codes=("evidence.corpus_empty",),
        )
    if selection.state is not RequirementSelectionState.RANKED:
        raise ValueError("applicable requirement must have RANKED or NO_EVIDENCE selection")

    selected = _selected_evidence(selection, evidence_by_id)
    aligned = tuple(item for item in selected if _aligned(requirement, item))
    supporting = tuple(
        item
        for item in aligned
        if item.polarity is requirement.polarity
        and _actor_is_decisive(requirement, item)
        and _strength_is_decisive(item)
    )
    contradicting = tuple(
        item
        for item in aligned
        if item.polarity is not requirement.polarity
        and _actor_is_decisive(requirement, item)
        and _strength_is_decisive(item)
    )
    ambiguous = tuple(
        item
        for item in aligned
        if item not in supporting and item not in contradicting
    )

    supporting_ids = tuple(item.evidence_id for item in supporting)
    contradicting_ids = tuple(item.evidence_id for item in contradicting)
    ambiguous_ids = tuple(item.evidence_id for item in ambiguous)

    if supporting and contradicting:
        return RequirementQualification(
            requirement_id=requirement.requirement_id,
            state=RequirementQualificationState.UNKNOWN,
            support=_bounds(_ZERO, _ONE),
            selection_complete=complete,
            ranked_evidence_ids=ranked_ids,
            supporting_evidence_ids=supporting_ids,
            contradicting_evidence_ids=contradicting_ids,
            ambiguous_evidence_ids=ambiguous_ids,
            reason_codes=("requirements.source_conflict",),
        )

    if contradicting:
        return RequirementQualification(
            requirement_id=requirement.requirement_id,
            state=RequirementQualificationState.CONTRADICTED,
            support=_bounds(_ZERO, _ZERO),
            selection_complete=complete,
            ranked_evidence_ids=ranked_ids,
            contradicting_evidence_ids=contradicting_ids,
            ambiguous_evidence_ids=ambiguous_ids,
            reason_codes=("requirements.explicit_contradiction",),
        )

    if supporting:
        threshold = requirement.threshold
        if (
            threshold is not None
            and threshold.metric is RequirementThresholdMetric.EXPERIENCE_YEARS
        ):
            years, incomplete_dates = _duration_years(supporting, as_of=as_of)
            minimum = threshold.minimum
            maximum = threshold.maximum
            if minimum is not None and years < minimum:
                if incomplete_dates:
                    return RequirementQualification(
                        requirement_id=requirement.requirement_id,
                        state=RequirementQualificationState.UNKNOWN,
                        support=_bounds(_ZERO, _ONE),
                        selection_complete=complete,
                        ranked_evidence_ids=ranked_ids,
                        supporting_evidence_ids=supporting_ids,
                        ambiguous_evidence_ids=ambiguous_ids,
                        reason_codes=("evidence.date_scope_unresolved",),
                    )
                if not complete:
                    return RequirementQualification(
                        requirement_id=requirement.requirement_id,
                        state=RequirementQualificationState.UNKNOWN,
                        support=_bounds(_ZERO, _ONE),
                        selection_complete=False,
                        ranked_evidence_ids=ranked_ids,
                        supporting_evidence_ids=supporting_ids,
                        ambiguous_evidence_ids=ambiguous_ids,
                        reason_codes=("evidence.selection_incomplete",),
                    )
                return RequirementQualification(
                    requirement_id=requirement.requirement_id,
                    state=RequirementQualificationState.NOT_EVIDENCED,
                    support=_bounds(_ZERO, _ONE),
                    selection_complete=True,
                    ranked_evidence_ids=ranked_ids,
                    supporting_evidence_ids=supporting_ids,
                    ambiguous_evidence_ids=ambiguous_ids,
                    reason_codes=("requirements.threshold_not_evidenced",),
                )
            if maximum is not None and years > maximum:
                return RequirementQualification(
                    requirement_id=requirement.requirement_id,
                    state=RequirementQualificationState.UNKNOWN,
                    support=_bounds(_ZERO, _ONE),
                    selection_complete=complete,
                    ranked_evidence_ids=ranked_ids,
                    supporting_evidence_ids=supporting_ids,
                    ambiguous_evidence_ids=ambiguous_ids,
                    reason_codes=("requirements.threshold_scope_unresolved",),
                )

        return RequirementQualification(
            requirement_id=requirement.requirement_id,
            state=RequirementQualificationState.MATCHED,
            support=_bounds(_ONE, _ONE),
            selection_complete=complete,
            ranked_evidence_ids=ranked_ids,
            supporting_evidence_ids=supporting_ids,
            ambiguous_evidence_ids=ambiguous_ids,
            reason_codes=("requirements.direct_support",),
        )

    if ambiguous:
        return RequirementQualification(
            requirement_id=requirement.requirement_id,
            state=RequirementQualificationState.UNKNOWN,
            support=_bounds(_ZERO, _ONE),
            selection_complete=complete,
            ranked_evidence_ids=ranked_ids,
            ambiguous_evidence_ids=ambiguous_ids,
            reason_codes=("evidence.scope_unresolved",),
        )

    if not complete:
        return RequirementQualification(
            requirement_id=requirement.requirement_id,
            state=RequirementQualificationState.UNKNOWN,
            support=_bounds(_ZERO, _ONE),
            selection_complete=False,
            ranked_evidence_ids=ranked_ids,
            reason_codes=("evidence.selection_incomplete",),
        )

    if requirement.subjects or requirement.activity is not None:
        return RequirementQualification(
            requirement_id=requirement.requirement_id,
            state=RequirementQualificationState.NOT_EVIDENCED,
            support=_bounds(_ZERO, _ONE),
            selection_complete=True,
            ranked_evidence_ids=ranked_ids,
            reason_codes=("requirements.subject_not_evidenced",),
        )

    return RequirementQualification(
        requirement_id=requirement.requirement_id,
        state=RequirementQualificationState.UNKNOWN,
        support=_bounds(_ZERO, _ONE),
        selection_complete=True,
        ranked_evidence_ids=ranked_ids,
        reason_codes=("requirements.semantic_alignment_unresolved",),
    )


def _group_qualifications(
    requirement_set: RequirementSet,
    evaluations: tuple[RequirementQualification, ...],
    ignored_requirement_ids: tuple[str, ...],
) -> tuple[RequirementGroupQualification, ...]:
    evaluation_by_id = {item.requirement_id: item for item in evaluations}
    ignored = set(ignored_requirement_ids)
    group_by_id = {item.group_id: item for item in requirement_set.groups}
    memo: dict[str, RequirementGroupQualification] = {}

    def requirement_bounds(requirement_id: str) -> SupportBounds:
        if requirement_id in ignored:
            return _bounds(_ONE, _ONE)
        return evaluation_by_id[requirement_id].support

    def evaluate(group: RequirementGroup) -> RequirementGroupQualification:
        cached = memo.get(group.group_id)
        if cached is not None:
            return cached

        if group.operator is RequirementGroupOperator.CONDITIONAL:
            result = RequirementGroupQualification(
                group_id=group.group_id,
                support=_bounds(_ZERO, _ONE),
                reason_codes=("requirements.condition_unresolved",),
            )
            memo[group.group_id] = result
            return result

        bounds = [requirement_bounds(item) for item in group.requirement_ids]
        bounds.extend(evaluate(group_by_id[item]).support for item in group.child_group_ids)
        if not bounds:
            support = _bounds(_ONE, _ONE)
        elif group.operator is RequirementGroupOperator.ALL:
            support = _bounds(
                min(item.lower for item in bounds),
                min(item.upper for item in bounds),
            )
        else:
            support = _bounds(
                max(item.lower for item in bounds),
                max(item.upper for item in bounds),
            )
        result = RequirementGroupQualification(group_id=group.group_id, support=support)
        memo[group.group_id] = result
        return result

    for group in requirement_set.groups:
        evaluate(group)
    return tuple(memo[item.group_id] for item in requirement_set.groups)


def qualify_requirements(
    *,
    input_fingerprint: str,
    requirement_set_ref_sha256: str,
    resume_evidence_set_ref_sha256: str,
    evidence_candidate_set_ref_sha256: str,
    requirement_set: RequirementSet,
    evidence_set: ResumeEvidenceSet,
    candidate_set: EvidenceCandidateSet,
    qualification_version: str,
    as_of: date,
) -> RequirementQualificationSet:
    """Qualify selected evidence without treating Jina relevance as entailment/probability."""

    selection_by_id = {item.requirement_id: item for item in candidate_set.selections}
    expected_ids = {item.requirement_id for item in requirement_set.requirements}
    if set(selection_by_id) != expected_ids:
        raise ValueError("EvidenceCandidateSet must contain exactly one selection per requirement")

    evaluations: list[RequirementQualification] = []
    ignored: list[str] = []
    for requirement in requirement_set.requirements:
        selection = selection_by_id[requirement.requirement_id]
        if requirement.modality is RequirementModality.NOT_REQUIRED:
            if selection.state is not RequirementSelectionState.SKIPPED_NOT_REQUIRED:
                raise ValueError("NOT_REQUIRED requirement must be skipped by P2-05")
            ignored.append(requirement.requirement_id)
            continue
        evaluations.append(_qualify_one(requirement, selection, evidence_set, as_of=as_of))

    evaluation_tuple = tuple(evaluations)
    ignored_tuple = tuple(ignored)
    return RequirementQualificationSet(
        input_fingerprint=input_fingerprint,
        requirement_set_sha256=requirement_set_ref_sha256,
        resume_evidence_set_sha256=resume_evidence_set_ref_sha256,
        evidence_candidate_set_sha256=evidence_candidate_set_ref_sha256,
        qualification_version=qualification_version,
        evaluations=evaluation_tuple,
        groups=_group_qualifications(requirement_set, evaluation_tuple, ignored_tuple),
        ignored_requirement_ids=ignored_tuple,
    )
