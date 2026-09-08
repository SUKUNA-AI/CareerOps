"""Runtime seam P2-03 → P2-04 для durable Processing worker"""

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
    RequirementSet,
    ResumeEvidenceSet,
)
from .core import evaluate_filter, extract_requirements, extract_resume_evidence
from .queue import ProcessingJobRecord
from .worker import ProcessingExecutionDisposition, ProcessingExecutionResult


class ProcessingInputLoader(Protocol):
    """Загружает pinned inputs и обязан проверить их identity и checksum"""

    async def load_manifest(self, uri: str) -> ProcessingInputManifest: ...

    async def load_vacancy(self, ref: NormalizedRef) -> NormalizedVacancy: ...

    async def load_resume(self, ref: NormalizedRef) -> NormalizedResume: ...


class P204ArtifactPublisher(Protocol):
    """Минимальная artifact boundary, необходимая executor до P2-04 включительно"""

    async def publish_filter_trace(
        self,
        manifest: ProcessingInputManifest,
        decision: FilterDecision,
    ) -> ProcessingArtifactRef: ...

    async def publish_requirement_set(
        self,
        requirement_set: RequirementSet,
    ) -> ProcessingArtifactRef: ...

    async def publish_resume_evidence_set(
        self,
        evidence_set: ResumeEvidenceSet,
    ) -> ProcessingArtifactRef: ...

    async def publish_p204_result(
        self,
        result: P204ResultArtifact,
    ) -> ProcessingArtifactRef: ...


class P204Executor:
    """Исполняет текущий детерминированный Processing horizon P2-03 + P2-04"""

    def __init__(
        self,
        *,
        loader: ProcessingInputLoader,
        publisher: P204ArtifactPublisher,
    ) -> None:
        self._loader = loader
        self._publisher = publisher

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
                requirements = extract_requirements(
                    vacancy,
                    extraction_version=manifest.versions.requirement_extraction_version,
                )
                evidence = extract_resume_evidence(
                    resume,
                    evidence_version=manifest.versions.evidence_version,
                )
            except ValueError:
                return self._terminal("processing.p204.extraction_invalid")

            requirement_ref = await self._publisher.publish_requirement_set(requirements)
            evidence_ref = await self._publisher.publish_resume_evidence_set(evidence)

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
