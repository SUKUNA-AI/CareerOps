from __future__ import annotations

import pytest

from careerops_processing.contracts import (
    EvidenceActorScope,
    EvidenceContext,
    EvidenceKind,
    EvidenceStrength,
    JinaVersionBundle,
    Requirement,
    RequirementContext,
    RequirementGroup,
    RequirementGroupOperator,
    RequirementImportance,
    RequirementKind,
    RequirementModality,
    RequirementSelectionState,
    RequirementSet,
    ResumeEvidence,
    ResumeEvidenceSet,
    SemanticPolarity,
    SemanticSourceRef,
    SemanticSubject,
)
from careerops_processing.core.reranking import (
    render_evidence_for_reranker,
    render_requirement_for_reranker,
)
from careerops_processing.selector import (
    EvidenceCandidateSelector,
    RerankerClient,
    RerankerProtocolError,
    RerankerResponse,
    RerankerScore,
    RerankerTokenBudgetError,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _source(path: str) -> SemanticSourceRef:
    return SemanticSourceRef(source_path=path, rendered_value="source")


def _requirement(
    requirement_id: str,
    statement: str,
    subject: str,
    *,
    modality: RequirementModality = RequirementModality.REQUIRED,
) -> Requirement:
    importance = (
        RequirementImportance.OPTIONAL
        if modality is RequirementModality.NOT_REQUIRED
        else RequirementImportance.MANDATORY
    )
    return Requirement(
        requirement_id=requirement_id,
        kind=RequirementKind.TECHNOLOGY,
        statement=statement,
        subjects=(SemanticSubject(text=subject, normalized=subject.casefold()),),
        context=RequirementContext.QUALIFICATION,
        importance=importance,
        modality=modality,
        polarity=SemanticPolarity.POSITIVE,
        source_refs=(_source(f"requirements.{requirement_id}"),),
    )


def _requirement_set(*requirements: Requirement) -> RequirementSet:
    return RequirementSet(
        source_key="hh",
        source_entity_id="vacancy-1",
        semantic_content_hash=HASH_A,
        normalized_schema_version="vacancy-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        extraction_version="requirements-v2",
        requirements=tuple(requirements),
        groups=(
            RequirementGroup(
                group_id="root",
                operator=RequirementGroupOperator.ALL,
                requirement_ids=tuple(item.requirement_id for item in requirements),
            ),
        ),
        root_group_id="root",
    )


def _evidence(evidence_id: str, statement: str, subject: str) -> ResumeEvidence:
    return ResumeEvidence(
        evidence_id=evidence_id,
        kind=EvidenceKind.EXPERIENCE,
        statement=statement,
        subjects=(SemanticSubject(text=subject, normalized=subject.casefold()),),
        actor_scope=EvidenceActorScope.SELF,
        context=EvidenceContext.COMMERCIAL,
        polarity=SemanticPolarity.POSITIVE,
        strength=EvidenceStrength.DIRECT,
        source_refs=(_source(f"evidence.{evidence_id}"),),
    )


def _evidence_set(*evidence: ResumeEvidence) -> ResumeEvidenceSet:
    return ResumeEvidenceSet(
        source_key="hh",
        account_key="account-1",
        source_entity_id="resume-1",
        semantic_content_hash=HASH_B,
        normalized_schema_version="resume-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        evidence_version="evidence-v2",
        evidence=tuple(evidence),
    )


def _jina(*, token_budget: int = 4096, top_k: int = 2) -> JinaVersionBundle:
    return JinaVersionBundle(
        model_id="jinaai/jina-reranker-v3.5",
        model_revision="model-rev",
        model_code_revision="code-rev",
        tokenizer_revision="tokenizer-rev",
        runtime_backend="transformers-cuda",
        dtype_or_quantization="float16",
        torch_version="2.8.0",
        transformers_version="4.57.3",
        rendering_version="p205-render-v1",
        selection_version="p205-selection-v1",
        block_protocol="single-list-v1",
        token_budget=token_budget,
        top_k=top_k,
    )


class _FakeClient(RerankerClient):
    def __init__(self, response: RerankerResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, tuple[str, ...], int, int, JinaVersionBundle]] = []

    async def rerank(
        self,
        *,
        query: str,
        documents: tuple[str, ...],
        top_n: int,
        token_budget: int,
        expected_version: JinaVersionBundle,
    ) -> RerankerResponse:
        self.calls.append((query, documents, top_n, token_budget, expected_version))
        return self.response


def test_reranker_rendering_ignores_provenance() -> None:
    first = _requirement("req-1", "Python обязателен", "Python")
    second = first.model_copy(update={"source_refs": (_source("different.path"),)})
    assert render_requirement_for_reranker(first) == render_requirement_for_reranker(second)

    evidence = _evidence("ev-1", "Разработал сервис на Python", "Python")
    evidence_copy = evidence.model_copy(
        update={"source_refs": (_source("different.evidence.path"),)}
    )
    assert render_evidence_for_reranker(evidence) == render_evidence_for_reranker(evidence_copy)


@pytest.mark.asyncio
async def test_selector_ranks_all_evidence_and_skips_not_required() -> None:
    requirements = _requirement_set(
        _requirement("req-python", "Python обязателен", "Python"),
        _requirement(
            "req-k8s",
            "Kubernetes не требуется",
            "Kubernetes",
            modality=RequirementModality.NOT_REQUIRED,
        ),
    )
    evidence = _evidence_set(
        _evidence("ev-a", "Работал с SQL", "SQL"),
        _evidence("ev-b", "Разработал сервис на Python", "Python"),
        _evidence("ev-c", "Команда использовала Kafka", "Kafka"),
    )
    client = _FakeClient(
        RerankerResponse(
            results=(
                RerankerScore(index=0, relevance_score=0.2),
                RerankerScore(index=1, relevance_score=0.9),
            ),
            total_tokens=300,
        )
    )
    selector = EvidenceCandidateSelector(client)

    result = await selector.select(
        input_fingerprint=HASH_C,
        requirement_set_ref_sha256=HASH_A,
        resume_evidence_set_ref_sha256=HASH_B,
        requirement_set=requirements,
        evidence_set=evidence,
        jina=_jina(),
    )

    assert len(client.calls) == 1
    ranked = next(item for item in result.selections if item.requirement_id == "req-python")
    assert ranked.state is RequirementSelectionState.RANKED
    assert [item.evidence_id for item in ranked.candidates] == ["ev-b", "ev-a"]
    assert [item.rank for item in ranked.candidates] == [1, 2]
    assert ranked.pool_evidence_ids == ("ev-a", "ev-b", "ev-c")
    assert ranked.pool_render_sha256 is not None

    skipped = next(item for item in result.selections if item.requirement_id == "req-k8s")
    assert skipped.state is RequirementSelectionState.SKIPPED_NOT_REQUIRED
    assert skipped.candidates == ()


@pytest.mark.asyncio
async def test_selector_handles_empty_evidence_without_calling_reranker() -> None:
    requirements = _requirement_set(
        _requirement("req-python", "Python обязателен", "Python")
    )
    client = _FakeClient(RerankerResponse(results=(), total_tokens=0))
    selector = EvidenceCandidateSelector(client)

    result = await selector.select(
        input_fingerprint=HASH_C,
        requirement_set_ref_sha256=HASH_A,
        resume_evidence_set_ref_sha256=HASH_B,
        requirement_set=requirements,
        evidence_set=_evidence_set(),
        jina=_jina(),
    )

    assert client.calls == []
    assert result.selections[0].state is RequirementSelectionState.NO_EVIDENCE


@pytest.mark.asyncio
async def test_selector_rejects_invalid_reranker_indices() -> None:
    requirements = _requirement_set(
        _requirement("req-python", "Python обязателен", "Python")
    )
    evidence = _evidence_set(_evidence("ev-a", "Python", "Python"))
    client = _FakeClient(
        RerankerResponse(
            results=(RerankerScore(index=4, relevance_score=0.8),),
            total_tokens=10,
        )
    )

    with pytest.raises(RerankerProtocolError, match="index"):
        await EvidenceCandidateSelector(client).select(
            input_fingerprint=HASH_C,
            requirement_set_ref_sha256=HASH_A,
            resume_evidence_set_ref_sha256=HASH_B,
            requirement_set=requirements,
            evidence_set=evidence,
            jina=_jina(top_k=1),
        )


@pytest.mark.asyncio
async def test_selector_enforces_pinned_token_budget() -> None:
    requirements = _requirement_set(
        _requirement("req-python", "Python обязателен", "Python")
    )
    evidence = _evidence_set(_evidence("ev-a", "Python", "Python"))
    client = _FakeClient(
        RerankerResponse(
            results=(RerankerScore(index=0, relevance_score=0.8),),
            total_tokens=101,
        )
    )

    with pytest.raises(RerankerTokenBudgetError):
        await EvidenceCandidateSelector(client).select(
            input_fingerprint=HASH_C,
            requirement_set_ref_sha256=HASH_A,
            resume_evidence_set_ref_sha256=HASH_B,
            requirement_set=requirements,
            evidence_set=evidence,
            jina=_jina(token_budget=100, top_k=1),
        )