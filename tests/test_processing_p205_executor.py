from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from careerops_processing.contracts import (
    BindingSnapshot,
    DataQualityStatus,
    EntityType,
    EvidenceCandidate,
    EvidenceCandidateSet,
    EvidenceKind,
    FilterOutcome,
    JinaVersionBundle,
    NormalizedRef,
    P204ResultArtifact,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
    ProcessingInputManifest,
    ProcessingVersionBundle,
    RawObservationRef,
    Requirement,
    RequirementContext,
    RequirementGroup,
    RequirementGroupOperator,
    RequirementImportance,
    RequirementKind,
    RequirementModality,
    RequirementSet,
    ResumeEvidence,
    ResumeEvidenceSet,
    SemanticPolarity,
    SemanticSourceRef,
    SemanticSubject,
    TargetPolicy,
)
from careerops_processing.contracts.reranking import (
    RequirementEvidenceCandidates,
    RequirementSelectionState,
)
from careerops_processing.executor import P204StageResult, P205Executor
from careerops_processing.selector import RerankerUnavailableError
from careerops_processing.worker import ProcessingExecutionDisposition

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64


def _artifact(
    kind: ProcessingArtifactKind,
    schema_version: str,
    digest: str,
) -> ProcessingArtifactRef:
    return ProcessingArtifactRef(
        kind=kind,
        schema_version=schema_version,
        uri=f"s3://careerops-artifacts/processing/{kind.value}/{digest}.json",
        sha256=digest,
        size_bytes=10,
    )


def _jina() -> JinaVersionBundle:
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
        token_budget=4096,
        top_k=3,
    )


def _normalized_ref(entity_type: EntityType) -> NormalizedRef:
    is_resume = entity_type is EntityType.RESUME
    return NormalizedRef(
        entity_type=entity_type,
        source_key="hh",
        source_entity_id="resume-1" if is_resume else "vacancy-1",
        account_key="account-1" if is_resume else None,
        raw=RawObservationRef(
            raw_uri=f"s3://careerops-raw/{entity_type.value}.json",
            raw_sha256=HASH_B if is_resume else HASH_A,
            observed_at=datetime(2026, 9, 9, 10, tzinfo=UTC),
        ),
        normalized_uri=f"s3://careerops-lake/{entity_type.value}.json",
        normalized_sha256=HASH_D if is_resume else HASH_C,
        semantic_content_hash=HASH_B if is_resume else HASH_A,
        schema_version=f"{entity_type.value}-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        materialization_key=f"{entity_type.value}:1",
        processing_ready=True,
        dq_status=DataQualityStatus.CLEAN,
    )


def _manifest(*, jina: JinaVersionBundle | None) -> ProcessingInputManifest:
    policy = TargetPolicy.from_content(
        target_key="de",
        schema_version="target-policy-v1",
        policy_version="policy-v1",
        content={"filtering": {"schema_version": 1}},
    )
    return ProcessingInputManifest(
        vacancy=_normalized_ref(EntityType.VACANCY),
        resume=_normalized_ref(EntityType.RESUME),
        binding=BindingSnapshot(
            binding_key="binding-1",
            binding_version=1,
            account_key="account-1",
            source_resume_id="resume-1",
            target_key="de",
        ),
        target_policy=policy,
        versions=ProcessingVersionBundle(
            pipeline_version="processing-v2-p205",
            dictionary_version="dict-v1",
            filter_version="filter-v1",
            requirement_extraction_version="requirements-v2",
            evidence_version="evidence-v2",
            qualification_version="qualification-v1",
            scoring_version="scoring-v1",
            calibration_version="calibration-unset",
            jina=jina,
        ),
        as_of=datetime(2026, 9, 9, 12, tzinfo=UTC),
    )


def _source(path: str) -> SemanticSourceRef:
    return SemanticSourceRef(source_path=path, rendered_value="Python")


def _requirements(manifest: ProcessingInputManifest) -> RequirementSet:
    requirement = Requirement(
        requirement_id="req-python",
        kind=RequirementKind.TECHNOLOGY,
        statement="Python обязателен",
        subjects=(SemanticSubject(text="Python", normalized="python"),),
        context=RequirementContext.QUALIFICATION,
        importance=RequirementImportance.MANDATORY,
        modality=RequirementModality.REQUIRED,
        polarity=SemanticPolarity.POSITIVE,
        source_refs=(_source("requirements"),),
    )
    return RequirementSet(
        source_key=manifest.vacancy.source_key,
        source_entity_id=manifest.vacancy.source_entity_id,
        semantic_content_hash=manifest.vacancy.semantic_content_hash,
        normalized_schema_version=manifest.vacancy.schema_version,
        normalization_version=manifest.vacancy.normalization_version,
        dictionary_version=manifest.vacancy.dictionary_version,
        extraction_version=manifest.versions.requirement_extraction_version,
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


def _evidence(manifest: ProcessingInputManifest) -> ResumeEvidenceSet:
    item = ResumeEvidence(
        evidence_id="ev-python",
        kind=EvidenceKind.EXPERIENCE,
        statement="Разработал сервис на Python",
        subjects=(SemanticSubject(text="Python", normalized="python"),),
        source_refs=(_source("experience"),),
    )
    return ResumeEvidenceSet(
        source_key=manifest.resume.source_key,
        account_key=manifest.resume.account_key or "",
        source_entity_id=manifest.resume.source_entity_id,
        semantic_content_hash=manifest.resume.semantic_content_hash,
        normalized_schema_version=manifest.resume.schema_version,
        normalization_version=manifest.resume.normalization_version,
        dictionary_version=manifest.resume.dictionary_version,
        evidence_version=manifest.versions.evidence_version,
        evidence=(item,),
    )


def _p204_stage(
    manifest: ProcessingInputManifest,
    *,
    outcome: FilterOutcome,
) -> P204StageResult:
    filter_ref = _artifact(
        ProcessingArtifactKind.FILTER_TRACE,
        "careerops.processing.filter-trace.v1",
        HASH_C,
    )
    requirement_ref = None
    evidence_ref = None
    if outcome is FilterOutcome.KEEP:
        requirement_ref = _artifact(
            ProcessingArtifactKind.REQUIREMENT_SET,
            "careerops.processing.requirement-set.v2",
            HASH_A,
        )
        evidence_ref = _artifact(
            ProcessingArtifactKind.RESUME_EVIDENCE_SET,
            "careerops.processing.resume-evidence-set.v2",
            HASH_B,
        )
    result = P204ResultArtifact(
        input_fingerprint=manifest.input_fingerprint(),
        manifest=manifest,
        filter_outcome=outcome,
        filter_trace_ref=filter_ref,
        requirement_extraction_version=manifest.versions.requirement_extraction_version,
        evidence_version=manifest.versions.evidence_version,
        requirement_set_ref=requirement_ref,
        resume_evidence_set_ref=evidence_ref,
    )
    return P204StageResult(
        manifest=manifest,
        result=result,
        result_ref=_artifact(
            ProcessingArtifactKind.P2_04_RESULT,
            "careerops.processing.p2-04-result.v2",
            HASH_D,
        ),
    )


class _P204:
    def __init__(self, stage: P204StageResult) -> None:
        self.stage = stage

    async def run_stage(self, _job: Any) -> P204StageResult:
        return self.stage


class _Loader:
    def __init__(self, requirements: RequirementSet, evidence: ResumeEvidenceSet) -> None:
        self.requirements = requirements
        self.evidence = evidence

    async def load_requirement_set(self, _ref: ProcessingArtifactRef) -> RequirementSet:
        return self.requirements

    async def load_resume_evidence_set(
        self,
        _ref: ProcessingArtifactRef,
    ) -> ResumeEvidenceSet:
        return self.evidence


class _Publisher:
    def __init__(self) -> None:
        self.result: Any = None
        self.candidates: EvidenceCandidateSet | None = None

    async def publish_evidence_candidate_set(
        self,
        candidate_set: EvidenceCandidateSet,
    ) -> ProcessingArtifactRef:
        self.candidates = candidate_set
        return _artifact(
            ProcessingArtifactKind.EVIDENCE_CANDIDATE_SET,
            "careerops.processing.evidence-candidate-set.v1",
            HASH_E,
        )

    async def publish_p205_result(self, result: Any) -> ProcessingArtifactRef:
        self.result = result
        return _artifact(
            ProcessingArtifactKind.P2_05_RESULT,
            "careerops.processing.p2-05-result.v1",
            HASH_C,
        )


class _Selector:
    async def select(self, **kwargs: Any) -> EvidenceCandidateSet:
        jina = kwargs["jina"]
        return EvidenceCandidateSet(
            input_fingerprint=kwargs["input_fingerprint"],
            requirement_set_sha256=kwargs["requirement_set_ref_sha256"],
            resume_evidence_set_sha256=kwargs["resume_evidence_set_ref_sha256"],
            jina=jina,
            selections=(
                RequirementEvidenceCandidates(
                    requirement_id="req-python",
                    state=RequirementSelectionState.RANKED,
                    query_text="query",
                    pool_evidence_ids=("ev-python",),
                    pool_render_sha256=HASH_D,
                    candidates=(
                        EvidenceCandidate(
                            evidence_id="ev-python",
                            rank=1,
                            relevance_score=0.9,
                        ),
                    ),
                ),
            ),
        )


class _UnavailableSelector:
    async def select(self, **_kwargs: Any) -> EvidenceCandidateSet:
        raise RerankerUnavailableError("offline")


@pytest.mark.asyncio
async def test_p205_keep_publishes_candidate_checkpoint() -> None:
    manifest = _manifest(jina=_jina())
    stage = _p204_stage(manifest, outcome=FilterOutcome.KEEP)
    publisher = _Publisher()
    executor = P205Executor(
        p204=_P204(stage),  # type: ignore[arg-type]
        artifact_loader=_Loader(_requirements(manifest), _evidence(manifest)),
        publisher=publisher,  # type: ignore[arg-type]
        selector=_Selector(),
    )

    result = await executor.execute(object())  # type: ignore[arg-type]

    assert result.disposition is ProcessingExecutionDisposition.SUCCEEDED
    assert result.result_artifact_uri is not None
    assert publisher.candidates is not None
    assert publisher.result.filter_outcome is FilterOutcome.KEEP


@pytest.mark.asyncio
async def test_p205_excluded_pair_skips_reranker_with_pinned_manifest() -> None:
    manifest = _manifest(jina=_jina())
    stage = _p204_stage(manifest, outcome=FilterOutcome.EXCLUDE_PROVEN)
    publisher = _Publisher()
    executor = P205Executor(
        p204=_P204(stage),  # type: ignore[arg-type]
        artifact_loader=_Loader(_requirements(manifest), _evidence(manifest)),
        publisher=publisher,  # type: ignore[arg-type]
        selector=_UnavailableSelector(),
    )

    result = await executor.execute(object())  # type: ignore[arg-type]

    assert result.disposition is ProcessingExecutionDisposition.SUCCEEDED
    assert publisher.candidates is None
    assert publisher.result.filter_outcome is FilterOutcome.EXCLUDE_PROVEN
    assert publisher.result.evidence_candidate_set_ref is None


@pytest.mark.asyncio
async def test_p205_keep_without_pinned_jina_is_terminal() -> None:
    manifest = _manifest(jina=None)
    stage = _p204_stage(manifest, outcome=FilterOutcome.KEEP)
    executor = P205Executor(
        p204=_P204(stage),  # type: ignore[arg-type]
        artifact_loader=_Loader(_requirements(manifest), _evidence(manifest)),
        publisher=_Publisher(),  # type: ignore[arg-type]
        selector=_Selector(),
    )

    result = await executor.execute(object())  # type: ignore[arg-type]

    assert result.disposition is ProcessingExecutionDisposition.TERMINAL_FAILURE
    assert result.error_category == "processing.p205.jina_version_missing"


@pytest.mark.asyncio
async def test_p205_reranker_unavailable_defers_job() -> None:
    manifest = _manifest(jina=_jina())
    stage = _p204_stage(manifest, outcome=FilterOutcome.KEEP)
    executor = P205Executor(
        p204=_P204(stage),  # type: ignore[arg-type]
        artifact_loader=_Loader(_requirements(manifest), _evidence(manifest)),
        publisher=_Publisher(),  # type: ignore[arg-type]
        selector=_UnavailableSelector(),
    )

    result = await executor.execute(object())  # type: ignore[arg-type]

    assert result.disposition is ProcessingExecutionDisposition.DEFERRED
    assert result.error_category == "processing.p205.reranker_unavailable"
    assert result.next_attempt_at is not None