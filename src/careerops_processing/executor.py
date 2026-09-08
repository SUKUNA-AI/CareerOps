"""Связь стадий P2-03 и P2-04 с постоянным Processing worker"""

from __future__ import annotations

from typing import Protocol

from .contracts import (
    FilterDecision,
    FilterOutcome,
    NormalizedRef,
    NormalizedResume,
    NormalizedVacancy,
    P204ResultArtifact,
    ProcessingArtifactRef,
    ProcessingInputManifest,
)
from .core import evaluate_filter
from .queue import ProcessingJobRecord
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

    async def execute(self, job: ProcessingJobRecord) -> ProcessingExecutionResult:
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
        return ProcessingExecutionResult(
            disposition=ProcessingExecutionDisposition.SUCCEEDED,
            result_artifact_uri=result_ref.uri,
        )
