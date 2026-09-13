"""Оркестрация listwise reranking для выбора evidence-кандидатов P2-05."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from time import perf_counter
from typing import Protocol
from uuid import UUID, uuid4

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
from .telemetry import reranker_request_id


class RerankerError(RuntimeError):
    """Базовая ошибка внешнего reranker backend."""


class RerankerUnavailableError(RerankerError):
    """Временная недоступность reranker backend."""


class RerankerProtocolError(RerankerError):
    """Ответ reranker нарушает контракт P2-05."""


class RerankerTokenBudgetError(RerankerProtocolError):
    """Один listwise запрос не помещается в зафиксированный token budget."""


@dataclass(frozen=True, slots=True)
class RerankerScore:
    index: int
    relevance_score: float


@dataclass(frozen=True, slots=True)
class ObservedRerankerRuntime:
    model_id: str
    model_revision: str
    model_code_revision: str
    tokenizer_revision: str
    runtime_backend: str
    dtype_or_quantization: str
    torch_version: str
    transformers_version: str

    def as_dict(self) -> dict[str, str]:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "model_code_revision": self.model_code_revision,
            "tokenizer_revision": self.tokenizer_revision,
            "runtime_backend": self.runtime_backend,
            "dtype_or_quantization": self.dtype_or_quantization,
            "torch_version": self.torch_version,
            "transformers_version": self.transformers_version,
        }


@dataclass(frozen=True, slots=True)
class RerankerResponse:
    results: tuple[RerankerScore, ...]
    total_tokens: int
    runtime: ObservedRerankerRuntime | None = None


class RerankerRunStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RerankerRunObservation:
    run_id: UUID
    input_fingerprint: str
    requirement_id: str
    query: str
    ordered_pool_evidence_ids: tuple[str, ...]
    pool_render_sha256: str
    top_k: int
    token_budget: int
    expected_version: JinaVersionBundle
    actual_runtime: ObservedRerankerRuntime | None
    selected_candidates: tuple[EvidenceCandidate, ...]
    total_tokens: int | None
    started_at: datetime
    finished_at: datetime
    latency_ms: int
    status: RerankerRunStatus
    error_class: str | None = None


class RerankerAuditRecorder(Protocol):
    async def record(self, observation: RerankerRunObservation) -> None: ...


class RerankerClient(Protocol):
    """Минимальная граница между Processing и отдельным reranker runtime."""

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
    """Выбирает top-k evidence для requirements без принятия match decision."""

    def __init__(
        self,
        client: RerankerClient,
        *,
        audit_recorder: RerankerAuditRecorder | None = None,
    ) -> None:
        self._client = client
        self._audit_recorder = audit_recorder

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

    @staticmethod
    def _finish_times(started_at: datetime, started_perf: float) -> tuple[datetime, int]:
        finished_at = datetime.now(UTC)
        latency_ms = max(0, round((perf_counter() - started_perf) * 1000))
        if finished_at < started_at:
            finished_at = started_at
        return finished_at, latency_ms

    async def _record(self, observation: RerankerRunObservation) -> None:
        if self._audit_recorder is not None:
            await self._audit_recorder.record(observation)

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

            assert pool_sha256 is not None
            top_n = min(jina.top_k, len(documents))
            run_id = uuid4()
            started_at = datetime.now(UTC)
            started_perf = perf_counter()
            response: RerankerResponse | None = None

            try:
                context_token = reranker_request_id.set(str(run_id))
                try:
                    response = await self._client.rerank(
                        query=query,
                        documents=documents,
                        top_n=top_n,
                        token_budget=jina.token_budget,
                        expected_version=jina,
                    )
                finally:
                    reranker_request_id.reset(context_token)

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
                if self._audit_recorder is not None and response.runtime is None:
                    raise RerankerProtocolError(
                        "reranker audit требует фактическую runtime identity"
                    )
            except RerankerError as exc:
                finished_at, latency_ms = self._finish_times(started_at, started_perf)
                await self._record(
                    RerankerRunObservation(
                        run_id=run_id,
                        input_fingerprint=input_fingerprint,
                        requirement_id=requirement.requirement_id,
                        query=query,
                        ordered_pool_evidence_ids=pool_ids,
                        pool_render_sha256=pool_sha256,
                        top_k=top_n,
                        token_budget=jina.token_budget,
                        expected_version=jina,
                        actual_runtime=None if response is None else response.runtime,
                        selected_candidates=(),
                        total_tokens=None if response is None else response.total_tokens,
                        started_at=started_at,
                        finished_at=finished_at,
                        latency_ms=latency_ms,
                        status=RerankerRunStatus.ERROR,
                        error_class=type(exc).__name__,
                    )
                )
                raise

            assert response is not None
            finished_at, latency_ms = self._finish_times(started_at, started_perf)
            await self._record(
                RerankerRunObservation(
                    run_id=run_id,
                    input_fingerprint=input_fingerprint,
                    requirement_id=requirement.requirement_id,
                    query=query,
                    ordered_pool_evidence_ids=pool_ids,
                    pool_render_sha256=pool_sha256,
                    top_k=top_n,
                    token_budget=jina.token_budget,
                    expected_version=jina,
                    actual_runtime=response.runtime,
                    selected_candidates=candidates,
                    total_tokens=response.total_tokens,
                    started_at=started_at,
                    finished_at=finished_at,
                    latency_ms=latency_ms,
                    status=RerankerRunStatus.SUCCESS,
                )
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
