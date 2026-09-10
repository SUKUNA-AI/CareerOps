from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from careerops_processing.contracts import (
    EvidenceActorScope,
    EvidenceCandidate,
    EvidenceCandidateSet,
    EvidenceContext,
    EvidenceKind,
    EvidenceStrength,
    JinaVersionBundle,
    MatchDecisionBundle,
    NormalizedVacancy,
    Requirement,
    RequirementContext,
    RequirementEvidenceCandidates,
    RequirementGroup,
    RequirementGroupOperator,
    RequirementImportance,
    RequirementKind,
    RequirementModality,
    RequirementQualificationSet,
    RequirementSelectionState,
    RequirementSet,
    RequirementThreshold,
    ResumeEvidence,
    ResumeEvidenceSet,
    SemanticPolarity,
    SemanticSourceRef,
    SemanticSubject,
    SemanticTimeSpan,
    TargetPolicy,
)
from careerops_processing.core import qualify_requirements, score_match

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def source(path: str, *, rendered_value: str = "source") -> SemanticSourceRef:
    return SemanticSourceRef(source_path=path, rendered_value=rendered_value)


def requirement(
    requirement_id: str,
    subject: str,
    *,
    importance: RequirementImportance = RequirementImportance.MANDATORY,
    kind: RequirementKind = RequirementKind.TECHNOLOGY,
    modality: RequirementModality | None = None,
    polarity: SemanticPolarity = SemanticPolarity.POSITIVE,
    threshold: RequirementThreshold | None = None,
    statement: str | None = None,
) -> Requirement:
    if modality is None:
        modality = {
            RequirementImportance.MANDATORY: RequirementModality.REQUIRED,
            RequirementImportance.PREFERRED: RequirementModality.PREFERRED,
            RequirementImportance.OPTIONAL: RequirementModality.OPTIONAL,
            RequirementImportance.UNKNOWN: RequirementModality.UNKNOWN,
        }[importance]
    return Requirement(
        requirement_id=requirement_id,
        kind=kind,
        statement=statement or f"Требуется {subject}",
        subjects=(SemanticSubject(text=subject, normalized=subject.casefold()),),
        context=RequirementContext.QUALIFICATION,
        importance=importance,
        modality=modality,
        polarity=polarity,
        threshold=threshold,
        source_refs=(source(f"requirements.{requirement_id}"),),
    )


def prohibited_requirement(requirement_id: str, subject: str) -> Requirement:
    return requirement(
        requirement_id,
        subject,
        modality=RequirementModality.PROHIBITED,
        polarity=SemanticPolarity.NEGATIVE,
        statement=f"Запрещён {subject}",
    )


def requirement_set(
    *requirements: Requirement,
    groups: tuple[RequirementGroup, ...] | None = None,
    root_group_id: str = "root",
) -> RequirementSet:
    resolved_groups = groups
    if resolved_groups is None and requirements:
        resolved_groups = (
            RequirementGroup(
                group_id="root",
                operator=RequirementGroupOperator.ALL,
                requirement_ids=tuple(item.requirement_id for item in requirements),
            ),
        )
    return RequirementSet(
        source_key="hh",
        source_entity_id="vacancy-1",
        semantic_content_hash=HASH_A,
        normalized_schema_version="vacancy-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        extraction_version="requirements-v2",
        requirements=tuple(requirements),
        groups=resolved_groups or (),
        root_group_id=root_group_id if resolved_groups else None,
    )


def evidence(
    evidence_id: str,
    subject: str,
    *,
    polarity: SemanticPolarity = SemanticPolarity.POSITIVE,
    actor_scope: EvidenceActorScope = EvidenceActorScope.SELF,
    strength: EvidenceStrength = EvidenceStrength.DIRECT,
    time_span: SemanticTimeSpan | None = None,
    kind: EvidenceKind = EvidenceKind.EXPERIENCE,
) -> ResumeEvidence:
    return ResumeEvidence(
        evidence_id=evidence_id,
        kind=kind,
        statement=f"Evidence {subject}",
        subjects=(SemanticSubject(text=subject, normalized=subject.casefold()),),
        actor_scope=actor_scope,
        context=EvidenceContext.COMMERCIAL,
        polarity=polarity,
        strength=strength,
        time_span=time_span,
        source_refs=(source(f"evidence.{evidence_id}"),),
    )


def evidence_set(*items: ResumeEvidence) -> ResumeEvidenceSet:
    return ResumeEvidenceSet(
        source_key="hh",
        account_key="account-1",
        source_entity_id="resume-1",
        semantic_content_hash=HASH_B,
        normalized_schema_version="resume-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        evidence_version="evidence-v2",
        evidence=tuple(items),
    )


def jina(*, top_k: int = 3) -> JinaVersionBundle:
    return JinaVersionBundle(
        model_id="jinaai/jina-reranker-v3.5",
        model_revision="model-rev",
        model_code_revision="code-rev",
        tokenizer_revision="tokenizer-rev",
        runtime_backend="transformers-cuda",
        dtype_or_quantization="float16",
        torch_version="2.14.0",
        transformers_version="4.57.3",
        rendering_version="p205-render-v1",
        selection_version="p205-selection-v1",
        block_protocol="single-list-v1",
        token_budget=4096,
        top_k=top_k,
    )


def candidate_set(
    requirements: RequirementSet,
    resume_evidence: ResumeEvidenceSet,
    *,
    scores: Mapping[str, float] | None = None,
    pool_ids_by_requirement: Mapping[str, tuple[str, ...]] | None = None,
    selected_ids_by_requirement: Mapping[str, tuple[str, ...]] | None = None,
) -> EvidenceCandidateSet:
    all_ids = tuple(item.evidence_id for item in resume_evidence.evidence)
    score_map = scores or {}
    pool_map = pool_ids_by_requirement or {}
    selected_map = selected_ids_by_requirement or {}
    selections: list[RequirementEvidenceCandidates] = []

    for item in requirements.requirements:
        if item.modality is RequirementModality.NOT_REQUIRED:
            selections.append(
                RequirementEvidenceCandidates(
                    requirement_id=item.requirement_id,
                    state=RequirementSelectionState.SKIPPED_NOT_REQUIRED,
                )
            )
            continue

        pool_ids = pool_map.get(item.requirement_id, all_ids)
        if not pool_ids:
            selections.append(
                RequirementEvidenceCandidates(
                    requirement_id=item.requirement_id,
                    state=RequirementSelectionState.NO_EVIDENCE,
                    query_text=item.statement,
                )
            )
            continue

        selected_ids = selected_map.get(item.requirement_id, pool_ids)
        selections.append(
            RequirementEvidenceCandidates(
                requirement_id=item.requirement_id,
                state=RequirementSelectionState.RANKED,
                query_text=item.statement,
                pool_evidence_ids=pool_ids,
                pool_render_sha256=HASH_D,
                candidates=tuple(
                    EvidenceCandidate(
                        evidence_id=evidence_id,
                        rank=rank,
                        relevance_score=score_map.get(evidence_id, 0.5),
                    )
                    for rank, evidence_id in enumerate(selected_ids, start=1)
                ),
            )
        )

    selected_count = max((len(item.candidates) for item in selections), default=0)
    return EvidenceCandidateSet(
        input_fingerprint=HASH_C,
        requirement_set_sha256=HASH_A,
        resume_evidence_set_sha256=HASH_B,
        jina=jina(top_k=max(selected_count, 1)),
        selections=tuple(selections),
    )


def qualify(
    requirements: RequirementSet,
    resume_evidence: ResumeEvidenceSet,
    candidates: EvidenceCandidateSet | None = None,
) -> RequirementQualificationSet:
    return qualify_requirements(
        input_fingerprint=HASH_C,
        requirement_set_ref_sha256=HASH_A,
        resume_evidence_set_ref_sha256=HASH_B,
        evidence_candidate_set_ref_sha256=HASH_D,
        requirement_set=requirements,
        evidence_set=resume_evidence,
        candidate_set=candidates or candidate_set(requirements, resume_evidence),
        qualification_version="qualification-v1",
        as_of=date(2026, 9, 10),
    )


def policy(
    *,
    calibrated: bool = True,
    threshold: str = "80",
    mandatory_min_support: str = "1",
    weights: Mapping[str, str] | None = None,
    filtering: Mapping[str, object] | None = None,
) -> TargetPolicy:
    content: dict[str, object] = {
        "filtering": {"schema_version": 1, **dict(filtering or {})}
    }
    if calibrated:
        content["scoring"] = {
            "schema_version": 1,
            "calibration_version": "gold-v1",
            "candidate_min_score": threshold,
            "mandatory_min_support": mandatory_min_support,
            "component_weights": dict(weights or {"mandatory_coverage": "1"}),
            "candidate_ttl_seconds": 3600,
        }
    return TargetPolicy.from_content(
        target_key="de",
        schema_version="target-policy-v1",
        policy_version="policy-v1",
        content=content,
    )


def score(
    requirements: RequirementSet,
    qualification: RequirementQualificationSet,
    *,
    calibrated: bool = True,
    threshold: str = "80",
    mandatory_min_support: str = "1",
    weights: Mapping[str, str] | None = None,
    filtering: Mapping[str, object] | None = None,
    vacancy: NormalizedVacancy | None = None,
) -> MatchDecisionBundle:
    return score_match(
        input_fingerprint=HASH_C,
        qualification_set_ref_sha256=HASH_D,
        requirement_set=requirements,
        qualification_set=qualification,
        target_policy=policy(
            calibrated=calibrated,
            threshold=threshold,
            mandatory_min_support=mandatory_min_support,
            weights=weights,
            filtering=filtering,
        ),
        scoring_version="scoring-v1",
        calibration_version="gold-v1" if calibrated else "calibration-unset",
        vacancy=vacancy,
    )


def empty_requirement_set() -> RequirementSet:
    return requirement_set()


def empty_qualification_set() -> RequirementQualificationSet:
    return RequirementQualificationSet(
        input_fingerprint=HASH_C,
        requirement_set_sha256=HASH_A,
        resume_evidence_set_sha256=HASH_B,
        evidence_candidate_set_sha256=HASH_D,
        qualification_version="qualification-v1",
    )
