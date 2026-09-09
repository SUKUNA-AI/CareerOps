"""Связь стадий P2-03–P2-05 с постоянным Processing worker"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .contracts import (
    EvidenceCandidateSet,
    FilterDecision,
    FilterOutcome,
    JinaVersionBundle,
    NormalizedRef,
    NormalizedResume,
    NormalizedVacancy,
    P204ResultArtifact,
    P205ResultArtifact,
    ProcessingArtifactRef,
    ProcessingInputManifest,
    RequirementSet,
    ResumeEvidenceSet,
)
from .core import evaluate_filter
from .queue import ProcessingJobRecord
from .selector import (
    RerankerProtocolError,
    RerankerTokenBudgetError,
    RerankerUnavailableError,
)
from .semantic_cache import SemanticArtifactResolutionError
from .worker import ProcessingExecutionDisposition, ProcessingExecutionResult


class ProcessingInputLoader(Protocol):
    """Загружает зафиксированные входы и проверяет их identity и checksum"""

    async def load_manifest(self, uri: str) -> ProcessingInputManifest: ...

    async def load_vacancy(self, ref: NormalizedRef) -> NormalizedVacancy: ...

    async def load_resume(self, ref: NormalizedRef) -> NormalizedResume: ...


class P204ArtifactPublisher(Protocol):
    """Публикует артефакты пары vacancy × resume до P2-04 включительно"""

    async def publish_filter_trace(
        self,
        manifest: ProcessingInputManifest,
        decision: FilterDecision,
    ) -> ProcessingArtifactRef: ...

    async def publish_p204_result(
        self,
        result: P204ResultArtifact,
    ) -> ProcessingArtifactRef: ...


class P205ArtifactPublisher(P204ArtifactPublisher, Protocol):
    """Публикует immutable artifacts P2-05"""

    async def publish_evidence_candidate_set(
        self,
        candidate_set: EvidenceCandidateSet,
    ) -> ProcessingArtifactRef: ...

    async def publish_p205_result(
        self,
        result: P205ResultArtifact,
    ) -> ProcessingArtifactRef: ...


class P204SemanticResolver(Protocol):
    """Переиспользует семантические артефакты между vacancy × resume jobs"""

    async def resolve_requirements(
        self,
        vacancy: NormalizedVacancy,
        *,
        extraction_version: str,
    ) -> ProcessingArtifactRef: ...

    async def resolve_evidence(
        self,
        resume: NormalizedResume,
        *,
        evidence_version: str,
    ) -> ProcessingArtifactRef: ...


class P205ArtifactLoader(Protocol):
    """Загружает typed P2-04 artifacts для reranking"""

    async def load_requirement_set(self, ref: ProcessingArtifactRef) -> RequirementSet: ...

    async def load_resume_evidence_set(
        self,
        ref: ProcessingArtifactRef,
    ) -> ResumeEvidenceSet: ...


class P205CandidateSelector(Protocol):
    """Выбирает evidence-кандидатов без qualification decision"""

    async def select(
        self,
        *,
        input_fingerprint: str,
        requirement_set_ref_sha256: str,
        resume_evidence_set_ref_sha256: str,
        requirement_set: RequirementSet,
        evidence_set: ResumeEvidenceSet,
        jina: JinaVersionBundle,
    ) -> EvidenceCandidateSet: ...


@dataclass(frozen=True, slots=True)
class P204StageResult:
    """Успешно опубликованная контрольная точка P2-04"""

    manifest: ProcessingInputManifest
    result: P204ResultArtifact
    result_ref: ProcessingArtifactRef


class P204Executor:
    """Исполняет детерминированные стадии P2-03 и P2-04"""

    def __init__(
        self,
        *,
        loader: ProcessingInputLoader,
        publisher: P204ArtifactPublisher,
        semantic_resolver: P204SemanticResolver,
    ) -> None:
        self._loader = loader
        self._publisher = publisher
        self._semantic_resolver = semantic_resolver

    @staticmethod
    def _job_matches_manifest(
        job: ProcessingJobRecord,
        manifest: ProcessingInputManifest,
    ) -> bool:
        return (
            job.input_fingerprint == manifest.input_fingerprint()
            and job.binding_version == manifest.binding.binding_version
            and job.pipeline_version == manifest.versions.pipeline_version
            and job.policy_version == manifest.target_policy.policy_version
        )

    @staticmethod
    def _terminal(error_category: str) -> ProcessingExecutionResult:
        return ProcessingExecutionResult(
            disposition=ProcessingExecutionDisposition.TERMINAL_FAILURE,
            error_category=error_category,
        )

    async def run_stage(
        self,
        job: ProcessingJobRecord,
    ) -> P204StageResult | ProcessingExecutionResult:
        try:
            manifest = await self._loader.load_manifest(job.input_manifest_uri)
        except FileNotFoundError:
            return self._terminal("processing.input_manifest_missing")
        except ValueError:
            return self._terminal("processing.input_manifest_invalid")

        if not self._job_matches_manifest(job, manifest):
            return self._terminal("processing.input_identity_mismatch")

        try:
            vacancy = await self._loader.load_vacancy(manifest.vacancy)
            resume = await self._loader.load_resume(manifest.resume)
        except FileNotFoundError:
            return self._terminal("processing.normalized_input_missing")
        except ValueError:
            return self._terminal("processing.normalized_input_invalid")

        decision = evaluate_filter(vacancy, manifest.target_policy, resume)
        filter_trace_ref = await self._publisher.publish_filter_trace(manifest, decision)

        requirement_ref: ProcessingArtifactRef | None = None
        evidence_ref: ProcessingArtifactRef | None = None

        if decision.outcome is FilterOutcome.KEEP:
            try:
                requirement_ref = await self._semantic_resolver.resolve_requirements(
                    vacancy,
                    extraction_version=manifest.versions.requirement_extraction_version,
                )
                evidence_ref = await self._semantic_resolver.resolve_evidence(
                    resume,
                    evidence_version=manifest.versions.evidence_version,
                )
            except SemanticArtifactResolutionError:
                return self._terminal("processing.p204.semantic_artifact_invalid")

        result = P204ResultArtifact(
            input_fingerprint=manifest.input_fingerprint(),
            manifest=manifest,
            filter_outcome=decision.outcome,
            filter_trace_ref=filter_trace_ref,
            requirement_extraction_version=manifest.versions.requirement_extraction_version,
            evidence_version=manifest.versions.evidence_version,
            requirement_set_ref=requirement_ref,
            resume_evidence_set_ref=evidence_ref,
        )
        result_ref = await self._publisher.publish_p204_result(result)
        return P204StageResult(
            manifest=manifest,
            result=result,
            result_ref=result_ref,
        )

    async def execute(self, job: ProcessingJobRecord) -> ProcessingExecutionResult:
        stage = await self.run_stage(job)
        if isinstance(stage, ProcessingExecutionResult):
            return stage
        return ProcessingExecutionResult(
            disposition=ProcessingExecutionDisposition.SUCCEEDED,
            result_artifact_uri=stage.result_ref.uri,
        )


class P205Executor:
    """Продолжает успешный P2-04 через evidence candidate selection P2-05"""

    def __init__(
        self,
        *,
        p204: P204Executor,
        artifact_loader: P205ArtifactLoader,
        publisher: P205ArtifactPublisher,
        selector: P205CandidateSelector,
        unavailable_delay: timedelta = timedelta(minutes=2),
    ) -> None:
        if unavailable_delay <= timedelta(0):
            raise ValueError("unavailable_delay должен быть положительным")
        self._p204 = p204
        self._artifact_loader = artifact_loader
        self._publisher = publisher
        self._selector = selector
        self._unavailable_delay = unavailable_delay

    @staticmethod
    def _terminal(error_category: str) -> ProcessingExecutionResult:
        return ProcessingExecutionResult(
            disposition=ProcessingExecutionDisposition.TERMINAL_FAILURE,
            error_category=error_category,
        )

    def _deferred(self, error_category: str) -> ProcessingExecutionResult:
        return ProcessingExecutionResult(
            disposition=ProcessingExecutionDisposition.DEFERRED,
            error_category=error_category,
            next_attempt_at=datetime.now(UTC) + self._unavailable_delay,
        )

    @staticmethod
    def _semantic_inputs_match_manifest(
        manifest: ProcessingInputManifest,
        requirements: RequirementSet,
        evidence: ResumeEvidenceSet,
    ) -> bool:
        return (
            requirements.source_key == manifest.vacancy.source_key
            and requirements.source_entity_id == manifest.vacancy.source_entity_id
            and requirements.semantic_content_hash == manifest.vacancy.semantic_content_hash
            and requirements.normalized_schema_version == manifest.vacancy.schema_version
            and requirements.normalization_version == manifest.vacancy.normalization_version
            and requirements.dictionary_version == manifest.versions.dictionary_version
            and requirements.extraction_version
            == manifest.versions.requirement_extraction_version
            and evidence.source_key == manifest.resume.source_key
            and evidence.account_key == manifest.resume.account_key
            and evidence.source_entity_id == manifest.resume.source_entity_id
            and evidence.semantic_content_hash == manifest.resume.semantic_content_hash
            and evidence.normalized_schema_version == manifest.resume.schema_version
            and evidence.normalization_version == manifest.resume.normalization_version
            and evidence.dictionary_version == manifest.versions.dictionary_version
            and evidence.evidence_version == manifest.versions.evidence_version
        )

    async def execute(self, job: ProcessingJobRecord) -> ProcessingExecutionResult:
        stage = await self._p204.run_stage(job)
        if isinstance(stage, ProcessingExecutionResult):
            return stage

        manifest = stage.manifest
        jina = manifest.versions.jina
        if jina is None:
            return self._terminal("processing.p205.jina_version_missing")

        if stage.result.filter_outcome is FilterOutcome.EXCLUDE_PROVEN:
            result = P205ResultArtifact(
                input_fingerprint=manifest.input_fingerprint(),
                manifest=manifest,
                filter_outcome=FilterOutcome.EXCLUDE_PROVEN,
                p2_04_result_ref=stage.result_ref,
            )
            result_ref = await self._publisher.publish_p205_result(result)
            return ProcessingExecutionResult(
                disposition=ProcessingExecutionDisposition.SUCCEEDED,
                result_artifact_uri=result_ref.uri,
            )

        requirement_ref = stage.result.requirement_set_ref
        evidence_ref = stage.result.resume_evidence_set_ref
        assert requirement_ref is not None
        assert evidence_ref is not None

        try:
            requirements = await self._artifact_loader.load_requirement_set(requirement_ref)
            evidence = await self._artifact_loader.load_resume_evidence_set(evidence_ref)
        except FileNotFoundError:
            return self._terminal("processing.p205.semantic_input_missing")
        except ValueError:
            return self._terminal("processing.p205.semantic_input_invalid")

        if not self._semantic_inputs_match_manifest(manifest, requirements, evidence):
            return self._terminal("processing.p205.semantic_input_identity_mismatch")

        try:
            candidate_set = await self._selector.select(
                input_fingerprint=manifest.input_fingerprint(),
                requirement_set_ref_sha256=requirement_ref.sha256,
                resume_evidence_set_ref_sha256=evidence_ref.sha256,
                requirement_set=requirements,
                evidence_set=evidence,
                jina=jina,
            )
        except RerankerUnavailableError:
            return self._deferred("processing.p205.reranker_unavailable")
        except RerankerTokenBudgetError:
            return self._terminal("processing.p205.token_budget_exceeded")
        except RerankerProtocolError:
            return self._terminal("processing.p205.reranker_protocol_invalid")

        candidate_ref = await self._publisher.publish_evidence_candidate_set(candidate_set)
        result = P205ResultArtifact(
            input_fingerprint=manifest.input_fingerprint(),
            manifest=manifest,
            filter_outcome=FilterOutcome.KEEP,
            p2_04_result_ref=stage.result_ref,
            evidence_candidate_set_ref=candidate_ref,
        )
        result_ref = await self._publisher.publish_p205_result(result)
        return ProcessingExecutionResult(
            disposition=ProcessingExecutionDisposition.SUCCEEDED,
            result_artifact_uri=result_ref.uri,
        )