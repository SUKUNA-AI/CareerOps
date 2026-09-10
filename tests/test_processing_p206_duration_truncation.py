from __future__ import annotations

from datetime import date
from decimal import Decimal

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
    RequirementThreshold,
    RequirementThresholdMetric,
    ResumeEvidence,
    ResumeEvidenceSet,
    SemanticPolarity,
    SemanticSourceRef,
    SemanticSubject,
    SemanticTimeSpan,
)
from careerops_processing.core import qualify_requirements

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _source(path: str) -> SemanticSourceRef:
    return SemanticSourceRef(source_path=path, rendered_value="source")


def _python_evidence(evidence_id: str, start: date, end: date) -> ResumeEvidence:
    return ResumeEvidence(
        evidence_id=evidence_id,
        kind=EvidenceKind.EXPERIENCE,
        statement="Работал с Python",
        subjects=(SemanticSubject(text="Python", normalized="python"),),
        actor_scope=EvidenceActorScope.SELF,
        context=EvidenceContext.COMMERCIAL,
        polarity=SemanticPolarity.POSITIVE,
        strength=EvidenceStrength.DIRECT,
        time_span=SemanticTimeSpan(
            start_date=start,
            end_date=end,
            currently_active=False,
        ),
        source_refs=(_source(f"evidence.{evidence_id}"),),
    )


def test_truncated_jina_selection_cannot_prove_insufficient_experience() -> None:
    requirement = Requirement(
        requirement_id="req-python-years",
        kind=RequirementKind.TECHNOLOGY,
        statement="Требуется 3 года Python",
        subjects=(SemanticSubject(text="Python", normalized="python"),),
        context=RequirementContext.QUALIFICATION,
        importance=RequirementImportance.MANDATORY,
        modality=RequirementModality.REQUIRED,
        polarity=SemanticPolarity.POSITIVE,
        threshold=RequirementThreshold(
            metric=RequirementThresholdMetric.EXPERIENCE_YEARS,
            minimum=Decimal("3"),
        ),
        source_refs=(_source("requirements.python"),),
    )
    requirement_set = RequirementSet(
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
    evidence_set = ResumeEvidenceSet(
        source_key="hh",
        account_key="account-1",
        source_entity_id="resume-1",
        semantic_content_hash=HASH_B,
        normalized_schema_version="resume-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        evidence_version="evidence-v2",
        evidence=(
            _python_evidence("ev-selected", date(2024, 1, 1), date(2025, 1, 1)),
            _python_evidence("ev-tail", date(2021, 1, 1), date(2024, 1, 1)),
        ),
    )
    candidate_set = EvidenceCandidateSet(
        input_fingerprint=HASH_C,
        requirement_set_sha256=HASH_A,
        resume_evidence_set_sha256=HASH_B,
        jina=JinaVersionBundle(
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
            top_k=1,
        ),
        selections=(
            RequirementEvidenceCandidates(
                requirement_id=requirement.requirement_id,
                state=RequirementSelectionState.RANKED,
                query_text=requirement.statement,
                pool_evidence_ids=("ev-selected", "ev-tail"),
                pool_render_sha256=HASH_D,
                candidates=(
                    EvidenceCandidate(
                        evidence_id="ev-selected",
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
        requirement_set=requirement_set,
        evidence_set=evidence_set,
        candidate_set=candidate_set,
        qualification_version="qualification-v1",
        as_of=date(2026, 9, 10),
    )

    evaluation = result.evaluations[0]
    assert evaluation.state is RequirementQualificationState.UNKNOWN
    assert evaluation.selection_complete is False
    assert evaluation.supporting_evidence_ids == ("ev-selected",)
    assert evaluation.reason_codes == ("evidence.selection_incomplete",)
