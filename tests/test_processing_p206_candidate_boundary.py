from __future__ import annotations

from datetime import date

from careerops_processing.contracts import (
    EvidenceActorScope,
    EvidenceCandidate,
    EvidenceCandidateSet,
    EvidenceContext,
    EvidenceKind,
    EvidenceStrength,
    JinaVersionBundle,
    Requirement,
    RequirementContext,
    RequirementEvidenceCandidates,
    RequirementGroup,
    RequirementGroupOperator,
    RequirementImportance,
    RequirementKind,
    RequirementModality,
    RequirementQualificationState,
    RequirementSelectionState,
    RequirementSet,
    ResumeEvidence,
    ResumeEvidenceSet,
    SemanticPolarity,
    SemanticSourceRef,
    SemanticSubject,
)
from careerops_processing.core import qualify_requirements

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _source(path: str) -> SemanticSourceRef:
    return SemanticSourceRef(source_path=path, rendered_value="source")


def _requirement_set() -> RequirementSet:
    requirement = Requirement(
        requirement_id="req-python",
        kind=RequirementKind.TECHNOLOGY,
        statement="Требуется Python",
        subjects=(SemanticSubject(text="Python", normalized="python"),),
        context=RequirementContext.QUALIFICATION,
        importance=RequirementImportance.MANDATORY,
        modality=RequirementModality.REQUIRED,
        polarity=SemanticPolarity.POSITIVE,
        source_refs=(_source("requirements.python"),),
    )
    return RequirementSet(
        source_key="hh",
        source_entity_id="vacancy-1",
        semantic_content_hash=HASH_A,
        normalized_schema_version="vacancy-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        extraction_version="requirements-v2",
        requirements=(requirement,),
        groups=(
            RequirementGroup(
                group_id="root",
                operator=RequirementGroupOperator.ALL,
                requirement_ids=(requirement.requirement_id,),
            ),
        ),
        root_group_id="root",
    )


def _evidence(evidence_id: str, subject: str) -> ResumeEvidence:
    return ResumeEvidence(
        evidence_id=evidence_id,
        kind=EvidenceKind.EXPERIENCE,
        statement=f"Работал с {subject}",
        subjects=(SemanticSubject(text=subject, normalized=subject.casefold()),),
        actor_scope=EvidenceActorScope.SELF,
        context=EvidenceContext.COMMERCIAL,
        polarity=SemanticPolarity.POSITIVE,
        strength=EvidenceStrength.DIRECT,
        source_refs=(_source(f"evidence.{evidence_id}"),),
    )


def _evidence_set() -> ResumeEvidenceSet:
    return ResumeEvidenceSet(
        source_key="hh",
        account_key="account-1",
        source_entity_id="resume-1",
        semantic_content_hash=HASH_B,
        normalized_schema_version="resume-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        evidence_version="evidence-v2",
        evidence=(
            _evidence("ev-python", "Python"),
            _evidence("ev-sql", "SQL"),
        ),
    )


def _jina(*, top_k: int = 1) -> JinaVersionBundle:
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


def _candidate_set(
    *,
    pool_ids: tuple[str, ...],
    selected_ids: tuple[str, ...] = ("ev-sql",),
) -> EvidenceCandidateSet:
    return EvidenceCandidateSet(
        input_fingerprint=HASH_C,
        requirement_set_sha256=HASH_A,
        resume_evidence_set_sha256=HASH_B,
        jina=_jina(top_k=len(selected_ids)),
        selections=(
            RequirementEvidenceCandidates(
                requirement_id="req-python",
                state=RequirementSelectionState.RANKED,
                query_text="Требуется Python",
                pool_evidence_ids=pool_ids,
                pool_render_sha256=HASH_D,
                candidates=tuple(
                    EvidenceCandidate(
                        evidence_id=evidence_id,
                        rank=rank,
                        relevance_score=1.0 / rank,
                    )
                    for rank, evidence_id in enumerate(selected_ids, start=1)
                ),
            ),
        ),
    )


def _qualify(candidate_set: EvidenceCandidateSet):
    return qualify_requirements(
        input_fingerprint=HASH_C,
        requirement_set_ref_sha256=HASH_A,
        resume_evidence_set_ref_sha256=HASH_B,
        evidence_candidate_set_ref_sha256=HASH_D,
        requirement_set=_requirement_set(),
        evidence_set=_evidence_set(),
        candidate_set=candidate_set,
        qualification_version="qualification-v1",
        as_of=date(2026, 9, 10),
    )


def test_p206_does_not_use_evidence_outside_jina_top_k() -> None:
    result = _qualify(_candidate_set(pool_ids=("ev-python", "ev-sql")))

    evaluation = result.evaluations[0]
    assert evaluation.state is RequirementQualificationState.UNKNOWN
    assert evaluation.selection_complete is False
    assert evaluation.ranked_evidence_ids == ("ev-sql",)
    assert evaluation.supporting_evidence_ids == ()
    assert evaluation.reason_codes == ("evidence.selection_incomplete",)


def test_incomplete_pre_jina_pool_cannot_prove_not_evidenced() -> None:
    result = _qualify(_candidate_set(pool_ids=("ev-sql",)))

    evaluation = result.evaluations[0]
    assert evaluation.state is RequirementQualificationState.UNKNOWN
    assert evaluation.selection_complete is False
    assert evaluation.ranked_evidence_ids == ("ev-sql",)
    assert evaluation.reason_codes == ("evidence.selection_incomplete",)


def test_exhaustive_jina_selection_can_prove_not_evidenced() -> None:
    evidence_set = ResumeEvidenceSet(
        source_key="hh",
        account_key="account-1",
        source_entity_id="resume-1",
        semantic_content_hash=HASH_B,
        normalized_schema_version="resume-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        evidence_version="evidence-v2",
        evidence=(_evidence("ev-sql", "SQL"),),
    )
    candidate_set = EvidenceCandidateSet(
        input_fingerprint=HASH_C,
        requirement_set_sha256=HASH_A,
        resume_evidence_set_sha256=HASH_B,
        jina=_jina(top_k=1),
        selections=(
            RequirementEvidenceCandidates(
                requirement_id="req-python",
                state=RequirementSelectionState.RANKED,
                query_text="Требуется Python",
                pool_evidence_ids=("ev-sql",),
                pool_render_sha256=HASH_D,
                candidates=(
                    EvidenceCandidate(
                        evidence_id="ev-sql",
                        rank=1,
                        relevance_score=0.9,
                    ),
                ),
            ),
        ),
    )

    result = qualify_requirements(
        input_fingerprint=HASH_C,
        requirement_set_ref_sha256=HASH_A,
        resume_evidence_set_ref_sha256=HASH_B,
        evidence_candidate_set_ref_sha256=HASH_D,
        requirement_set=_requirement_set(),
        evidence_set=evidence_set,
        candidate_set=candidate_set,
        qualification_version="qualification-v1",
        as_of=date(2026, 9, 10),
    )

    evaluation = result.evaluations[0]
    assert evaluation.state is RequirementQualificationState.NOT_EVIDENCED
    assert evaluation.selection_complete is True
    assert evaluation.reason_codes == ("requirements.subject_not_evidenced",)
