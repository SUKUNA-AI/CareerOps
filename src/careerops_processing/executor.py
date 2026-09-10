"""Связь стадий P2-03–P2-07 с постоянным Processing worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from .contracts import (
    EvidenceCandidateSet,
    FilterDecision,
    FilterOutcome,
    JinaVersionBundle,
    MatchDecision,
    MatchDecisionBundle,
    NormalizedRef,
    NormalizedResume,
    NormalizedVacancy,
    P204ResultArtifact,
    P205ResultArtifact,
    P206ResultArtifact,
    P207ResultArtifact,
    ProcessingArtifactRef,
    ProcessingInputManifest,
    RequirementQualificationSet,
    RequirementSet,
    ResumeEvidenceSet,
    ScoreBounds,
    ScoringPolicy,
)
from .core import evaluate_filter, qualify_requirements, score_match
from .queue import ProcessingJobRecord
from .selector import (
    RerankerProtocolError,
    RerankerTokenBudgetError,
    RerankerUnavailableError,
)
from .semantic_cache import SemanticArtifactResolutionError
from .worker import ProcessingExecutionDisposition, ProcessingExecutionResult


class ProcessingInputLoader(Protocol):
    """Загружает зафиксированные входы и проверяет их identity и checksum."""

    async def load_manifest(self, uri: str) -> ProcessingInputManifest: ...

    async def load_vacancy(self, ref: NormalizedRef) -> NormalizedVacancy: ...

    async def load_resume(self, ref: NormalizedRef) -> NormalizedResume: ...


class P204ArtifactPublisher(Protocol):
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
    async def publish_evidence_candidate_set(
        self,
        candidate_set: EvidenceCandidateSet,
    ) -> ProcessingArtifactRef: ...

    async def publish_p205_result(
        self,
        result: P205ResultArtifact,
    ) -> ProcessingArtifactRef: ...


class P206ArtifactPublisher(P205ArtifactPublisher, Protocol):
    async def publish_requirement_qualification_set(
        self,
        qualification_set: RequirementQualificationSet,
    ) -> ProcessingArtifactRef: ...

    async def publish_p206_result(
        self,
        result: P206ResultArtifact,
    ) -> ProcessingArtifactRef: ...


class P207ArtifactPublisher(P206ArtifactPublisher, Protocol):
    async def publish_match_decision(
        self,
        decision: MatchDecisionBundle,
    ) -> ProcessingArtifactRef: ...

    async def publish_p207_result(
        self,
        result: P207ResultArtifact,
    ) -> ProcessingArtifactRef: ...


class P204SemanticResolver(Protocol):
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


class ProcessingArtifactReader(Protocol):
    async def load_requirement_set(self, ref: ProcessingArtifactRef) -> RequirementSet: ...

    async def load_resume_evidence_set(
        self,
        ref: ProcessingArtifactRef,
    ) -> ResumeEvidenceSet: ...

    async def load_evidence_candidate_set(
        self,
        ref: ProcessingArtifactRef,
    ) -> EvidenceCandidateSet: ...

    async def load_requirement_qualification_set(
        self,
        ref: ProcessingArtifactRef,
    ) -> RequirementQualificationSet: ...


class P205CandidateSelector(Protocol):
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


class P207CurrentPublisher(Protocol):
    async def publish_current(
        self,
        job: ProcessingJobRecord,
        *,
        decision: MatchDecisionBundle,
        result_ref: ProcessingArtifactRef,
        candidate_ttl_seconds: int | None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class P204StageResult:
    manifest: ProcessingInputManifest
    result: P204ResultArtifact
    result_ref: ProcessingArtifactRef
    vacancy: NormalizedVacancy | None = None


@dataclass(frozen=True, slots=True)
class P205StageResult:
    manifest: ProcessingInputManifest
    result: P205ResultArtifact
    result_ref: ProcessingArtifactRef
    p204_stage: P204StageResult


@dataclass(frozen=True, slots=True)
class P206StageResult:
    manifest: ProcessingInputManifest
    result: P206ResultArtifact
    result_ref: ProcessingArtifactRef
    p205_stage: P205StageResult
    qualification_set: RequirementQualificationSet | None = None
    qualification_ref: ProcessingArtifactRef | None = None


class P204Executor:
    """Исполняет детерминированные стадии P2-03 и P2-04."""

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
        try:
            filter_trace_ref = await self._publisher.publish_filter_trace(manifest, decision)
        except ValueError:
            return self._terminal("processing.p204.artifact_integrity_invalid")

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
        try:
            result_ref = await self._publisher.publish_p204_result(result)
        except ValueError:
            return self._terminal("processing.p204.artifact_integrity_invalid")
        return P204StageResult(
            manifest=manifest,
            result=result,
            result_ref=result_ref,
            vacancy=vacancy,
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
    """P2-05 evidence selection: Jina ranks evidence but never qualifies it."""

    def __init__(
        self,
        *,
        p204: P204Executor,
        artifact_loader: ProcessingArtifactReader,
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

    async def run_stage(
        self,
        job: ProcessingJobRecord,
    ) -> P205StageResult | ProcessingExecutionResult:
        p204_stage = await self._p204.run_stage(job)
        if isinstance(p204_stage, ProcessingExecutionResult):
            return p204_stage

        manifest = p204_stage.manifest
        jina = manifest.versions.jina
        if jina is None:
            return self._terminal("processing.p205.jina_version_missing")

        if p204_stage.result.filter_outcome is FilterOutcome.EXCLUDE_PROVEN:
            result = P205ResultArtifact(
                input_fingerprint=manifest.input_fingerprint(),
                manifest=manifest,
                filter_outcome=FilterOutcome.EXCLUDE_PROVEN,
                p2_04_result_ref=p204_stage.result_ref,
            )
            try:
                result_ref = await self._publisher.publish_p205_result(result)
            except ValueError:
                return self._terminal("processing.p205.artifact_integrity_invalid")
            return P205StageResult(
                manifest=manifest,
                result=result,
                result_ref=result_ref,
                p204_stage=p204_stage,
            )

        requirement_ref = p204_stage.result.requirement_set_ref
        evidence_ref = p204_stage.result.resume_evidence_set_ref
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

        try:
            candidate_ref = await self._publisher.publish_evidence_candidate_set(candidate_set)
        except ValueError:
            return self._terminal("processing.p205.artifact_integrity_invalid")
        result = P205ResultArtifact(
            input_fingerprint=manifest.input_fingerprint(),
            manifest=manifest,
            filter_outcome=FilterOutcome.KEEP,
            p2_04_result_ref=p204_stage.result_ref,
            evidence_candidate_set_ref=candidate_ref,
        )
        try:
            result_ref = await self._publisher.publish_p205_result(result)
        except ValueError:
            return self._terminal("processing.p205.artifact_integrity_invalid")
        return P205StageResult(
            manifest=manifest,
            result=result,
            result_ref=result_ref,
            p204_stage=p204_stage,
        )

    async def execute(self, job: ProcessingJobRecord) -> ProcessingExecutionResult:
        stage = await self.run_stage(job)
        if isinstance(stage, ProcessingExecutionResult):
            return stage
        return ProcessingExecutionResult(
            disposition=ProcessingExecutionDisposition.SUCCEEDED,
            result_artifact_uri=stage.result_ref.uri,
        )


class P206Executor:
    """P2-06 deterministic qualification over P2-04 semantics and P2-05 selection."""

    def __init__(
        self,
        *,
        p205: P205Executor,
        artifact_loader: ProcessingArtifactReader,
        publisher: P206ArtifactPublisher,
    ) -> None:
        self._p205 = p205
        self._artifact_loader = artifact_loader
        self._publisher = publisher

    @staticmethod
    def _terminal(error_category: str) -> ProcessingExecutionResult:
        return ProcessingExecutionResult(
            disposition=ProcessingExecutionDisposition.TERMINAL_FAILURE,
            error_category=error_category,
        )

    async def run_stage(
        self,
        job: ProcessingJobRecord,
    ) -> P206StageResult | ProcessingExecutionResult:
        p205_stage = await self._p205.run_stage(job)
        if isinstance(p205_stage, ProcessingExecutionResult):
            return p205_stage

        manifest = p205_stage.manifest
        if p205_stage.result.filter_outcome is FilterOutcome.EXCLUDE_PROVEN:
            result = P206ResultArtifact(
                input_fingerprint=manifest.input_fingerprint(),
                manifest=manifest,
                filter_outcome=FilterOutcome.EXCLUDE_PROVEN,
                p2_05_result_ref=p205_stage.result_ref,
            )
            try:
                result_ref = await self._publisher.publish_p206_result(result)
            except ValueError:
                return self._terminal("processing.p206.artifact_integrity_invalid")
            return P206StageResult(
                manifest=manifest,
                result=result,
                result_ref=result_ref,
                p205_stage=p205_stage,
            )

        p204_result = p205_stage.p204_stage.result
        requirement_ref = p204_result.requirement_set_ref
        evidence_ref = p204_result.resume_evidence_set_ref
        candidate_ref = p205_stage.result.evidence_candidate_set_ref
        assert requirement_ref is not None
        assert evidence_ref is not None
        assert candidate_ref is not None

        try:
            requirements = await self._artifact_loader.load_requirement_set(requirement_ref)
            evidence = await self._artifact_loader.load_resume_evidence_set(evidence_ref)
            candidates = await self._artifact_loader.load_evidence_candidate_set(candidate_ref)
        except FileNotFoundError:
            return self._terminal("processing.p206.input_artifact_missing")
        except ValueError:
            return self._terminal("processing.p206.input_artifact_invalid")

        jina = manifest.versions.jina
        if (
            candidates.input_fingerprint != manifest.input_fingerprint()
            or candidates.requirement_set_sha256 != requirement_ref.sha256
            or candidates.resume_evidence_set_sha256 != evidence_ref.sha256
            or jina is None
            or candidates.jina != jina
        ):
            return self._terminal("processing.p206.input_identity_mismatch")

        try:
            qualification_set = qualify_requirements(
                input_fingerprint=manifest.input_fingerprint(),
                requirement_set_ref_sha256=requirement_ref.sha256,
                resume_evidence_set_ref_sha256=evidence_ref.sha256,
                evidence_candidate_set_ref_sha256=candidate_ref.sha256,
                requirement_set=requirements,
                evidence_set=evidence,
                candidate_set=candidates,
                qualification_version=manifest.versions.qualification_version,
                as_of=manifest.as_of.date(),
            )
        except ValueError:
            return self._terminal("processing.p206.qualification_contract_invalid")

        try:
            qualification_ref = await self._publisher.publish_requirement_qualification_set(
                qualification_set
            )
        except ValueError:
            return self._terminal("processing.p206.artifact_integrity_invalid")
        result = P206ResultArtifact(
            input_fingerprint=manifest.input_fingerprint(),
            manifest=manifest,
            filter_outcome=FilterOutcome.KEEP,
            p2_05_result_ref=p205_stage.result_ref,
            requirement_qualification_set_ref=qualification_ref,
        )
        try:
            result_ref = await self._publisher.publish_p206_result(result)
        except ValueError:
            return self._terminal("processing.p206.artifact_integrity_invalid")
        return P206StageResult(
            manifest=manifest,
            result=result,
            result_ref=result_ref,
            p205_stage=p205_stage,
            qualification_set=qualification_set,
            qualification_ref=qualification_ref,
        )

    async def execute(self, job: ProcessingJobRecord) -> ProcessingExecutionResult:
        stage = await self.run_stage(job)
        if isinstance(stage, ProcessingExecutionResult):
            return stage
        return ProcessingExecutionResult(
            disposition=ProcessingExecutionDisposition.SUCCEEDED,
            result_artifact_uri=stage.result_ref.uri,
        )


class P207Executor:
    """P2-07 deterministic scoring, decision artifact and fenced current publication."""

    def __init__(
        self,
        *,
        p206: P206Executor,
        artifact_loader: ProcessingArtifactReader,
        publisher: P207ArtifactPublisher,
        current_publisher: P207CurrentPublisher,
    ) -> None:
        self._p206 = p206
        self._artifact_loader = artifact_loader
        self._publisher = publisher
        self._current_publisher = current_publisher

    @staticmethod
    def _terminal(error_category: str) -> ProcessingExecutionResult:
        return ProcessingExecutionResult(
            disposition=ProcessingExecutionDisposition.TERMINAL_FAILURE,
            error_category=error_category,
        )

    async def execute(self, job: ProcessingJobRecord) -> ProcessingExecutionResult:
        p206_stage = await self._p206.run_stage(job)
        if isinstance(p206_stage, ProcessingExecutionResult):
            return p206_stage

        manifest = p206_stage.manifest
        candidate_ttl_seconds: int | None = None

        if p206_stage.result.filter_outcome is FilterOutcome.EXCLUDE_PROVEN:
            decision = MatchDecisionBundle(
                input_fingerprint=manifest.input_fingerprint(),
                requirement_qualification_set_sha256=None,
                scoring_version=manifest.versions.scoring_version,
                calibration_version=manifest.versions.calibration_version,
                policy_version=manifest.target_policy.policy_version,
                decision=MatchDecision.SKIP,
                score=ScoreBounds(lower=Decimal("0"), upper=Decimal("0")),
                deterministic_score=Decimal("0"),
                reason_codes=("filter.proven_exclusion",),
            )
        else:
            qualification_set = p206_stage.qualification_set
            qualification_ref = p206_stage.qualification_ref
            requirement_ref = p206_stage.p205_stage.p204_stage.result.requirement_set_ref
            assert qualification_set is not None
            assert qualification_ref is not None
            assert requirement_ref is not None

            try:
                requirements = await self._artifact_loader.load_requirement_set(requirement_ref)
            except FileNotFoundError:
                return self._terminal("processing.p207.input_artifact_missing")
            except ValueError:
                return self._terminal("processing.p207.input_artifact_invalid")

            if (
                qualification_set.input_fingerprint != manifest.input_fingerprint()
                or qualification_set.requirement_set_sha256 != requirement_ref.sha256
                or qualification_set.qualification_version
                != manifest.versions.qualification_version
            ):
                return self._terminal("processing.p207.input_identity_mismatch")

            try:
                decision = score_match(
                    input_fingerprint=manifest.input_fingerprint(),
                    qualification_set_ref_sha256=qualification_ref.sha256,
                    requirement_set=requirements,
                    qualification_set=qualification_set,
                    target_policy=manifest.target_policy,
                    scoring_version=manifest.versions.scoring_version,
                    calibration_version=manifest.versions.calibration_version,
                    vacancy=p206_stage.p205_stage.p204_stage.vacancy,
                )
                if decision.decision is MatchDecision.APPLICATION_CANDIDATE:
                    scoring_policy = ScoringPolicy.from_target_policy(manifest.target_policy)
                    if scoring_policy is None:
                        return self._terminal("processing.p207.scoring_policy_missing")
                    candidate_ttl_seconds = scoring_policy.candidate_ttl_seconds
            except ValueError:
                return self._terminal("processing.p207.scoring_contract_invalid")

        try:
            decision_ref = await self._publisher.publish_match_decision(decision)
        except ValueError:
            return self._terminal("processing.p207.artifact_integrity_invalid")
        result = P207ResultArtifact(
            input_fingerprint=manifest.input_fingerprint(),
            manifest=manifest,
            filter_outcome=p206_stage.result.filter_outcome,
            p2_06_result_ref=p206_stage.result_ref,
            match_decision_ref=decision_ref,
        )
        try:
            result_ref = await self._publisher.publish_p207_result(result)
        except ValueError:
            return self._terminal("processing.p207.artifact_integrity_invalid")

        try:
            await self._current_publisher.publish_current(
                job,
                decision=decision,
                result_ref=result_ref,
                candidate_ttl_seconds=candidate_ttl_seconds,
            )
        except ValueError:
            return self._terminal("processing.p207.current_publication_invalid")
        return ProcessingExecutionResult(
            disposition=ProcessingExecutionDisposition.SUCCEEDED,
            result_artifact_uri=result_ref.uri,
        )
