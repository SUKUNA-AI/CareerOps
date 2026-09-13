"""Оркестрация listwise reranking для выбора evidence-кандидатов P2-05"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from .contracts import (
    EvidenceCandidate,
    EvidenceCandidateSet,
    JinaVersionBundle,
    RequirementEvidenceCandidates,
    RequirementModality,
    RequirementSelectionState,
    RequirementSet,
    ResumeEvidenceSet,
)
from .core.reranking import (
    evidence_pool_render_sha256,
    render_evidence_for_reranker,
    render_requirement_for_reranker,
)


class RerankerError(RuntimeError):
    """Базовая ошибка внешнего reranker backend"""


class RerankerUnavailableError(RerankerError):
    """Временная недоступность reranker backend"""


class RerankerProtocolError(RerankerError):
    """Ответ reranker нарушает контракт P2-05"""


class RerankerTokenBudgetError(RerankerProtocolError):
    """Один listwise запрос не помещается в зафиксированный token budget"""


@dataclass(frozen=True, slots=True)
class RerankerScore:
    index: int
    relevance_score: float


@dataclass(frozen=True, slots=True)
class RerankerResponse:
    results: tuple[RerankerScore, ...]
    total_tokens: int


class RerankerClient(Protocol):
    """Минимальная граница между Processing и отдельным reranker runtime"""

    async def rerank(
        self,
        *,
        query: str,
        documents: tuple[str, ...],
        top_n: int,
        token_budget: int,
        expected_version: JinaVersionBundle,
    ) -> RerankerResponse: ...


class EvidenceCandidateSelector:
    """Выбирает top-k evidence для requirements без принятия match decision"""

    def __init__(self, client: RerankerClient) -> None:
        self._client = client

    @staticmethod
    def _validate_response(
        response: RerankerResponse,
        *,
        document_count: int,
        expected_count: int,
        token_budget: int,
    ) -> tuple[RerankerScore, ...]:
        if response.total_tokens < 0:
            raise RerankerProtocolError("reranker total_tokens не должен быть отрицательным")
        if response.total_tokens > token_budget:
            raise RerankerTokenBudgetError("reranker request превысил token budget")
        if len(response.results) != expected_count:
            raise RerankerProtocolError("reranker вернул неожиданное число результатов")

        indices = [item.index for item in response.results]
        if len(indices) != len(set(indices)):
            raise RerankerProtocolError("reranker вернул повторяющиеся document indices")
        for item in response.results:
            if item.index < 0 or item.index >= document_count:
                raise RerankerProtocolError("reranker вернул document index вне candidate pool")
            if not math.isfinite(item.relevance_score):
                raise RerankerProtocolError("reranker вернул нечисловой relevance score")

        return tuple(
            sorted(
                response.results,
                key=lambda item: (-item.relevance_score, item.index),
            )
        )

    async def select(
        self,
        *,
        input_fingerprint: str,
        requirement_set_ref_sha256: str,
        resume_evidence_set_ref_sha256: str,
        requirement_set: RequirementSet,
        evidence_set: ResumeEvidenceSet,
        jina: JinaVersionBundle,
    ) -> EvidenceCandidateSet:
        if jina.block_protocol != "single-list-v1":
            raise RerankerProtocolError("P2-05 пока поддерживает только single-list-v1")

        ordered_evidence = tuple(sorted(evidence_set.evidence, key=lambda item: item.evidence_id))
        rendered_pool = tuple(
            (item.evidence_id, render_evidence_for_reranker(item))
            for item in ordered_evidence
        )
        pool_ids = tuple(item[0] for item in rendered_pool)
        documents = tuple(item[1] for item in rendered_pool)
        pool_sha256 = (
            evidence_pool_render_sha256(rendered_pool)
            if rendered_pool
            else None
        )

        selections: list[RequirementEvidenceCandidates] = []
        for requirement in requirement_set.requirements:
            if requirement.modality is RequirementModality.NOT_REQUIRED:
                selections.append(
                    RequirementEvidenceCandidates(
                        requirement_id=requirement.requirement_id,
                        state=RequirementSelectionState.SKIPPED_NOT_REQUIRED,
                    )
                )
                continue

            query = render_requirement_for_reranker(requirement)
            if not documents:
                selections.append(
                    RequirementEvidenceCandidates(
                        requirement_id=requirement.requirement_id,
                        state=RequirementSelectionState.NO_EVIDENCE,
                        query_text=query,
                    )
                )
                continue

            top_n = min(jina.top_k, len(documents))
            response = await self._client.rerank(
                query=query,
                documents=documents,
                top_n=top_n,
                token_budget=jina.token_budget,
                expected_version=jina,
            )
            ranked = self._validate_response(
                response,
                document_count=len(documents),
                expected_count=top_n,
                token_budget=jina.token_budget,
            )
            candidates = tuple(
                EvidenceCandidate(
                    evidence_id=pool_ids[item.index],
                    rank=rank,
                    relevance_score=item.relevance_score,
                )
                for rank, item in enumerate(ranked, start=1)
            )
            selections.append(
                RequirementEvidenceCandidates(
                    requirement_id=requirement.requirement_id,
                    state=RequirementSelectionState.RANKED,
                    query_text=query,
                    pool_evidence_ids=pool_ids,
                    pool_render_sha256=pool_sha256,
                    candidates=candidates,
                )
            )

        return EvidenceCandidateSet(
            input_fingerprint=input_fingerprint,
            requirement_set_sha256=requirement_set_ref_sha256,
            resume_evidence_set_sha256=resume_evidence_set_ref_sha256,
            jina=jina,
            selections=tuple(selections),
        )
