from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from support.processing import (
    candidate_set,
    evidence,
    evidence_set,
    jina,
    policy,
    requirement,
    requirement_set,
)

from careerops_processing.contracts import (
    BindingSnapshot,
    DataQualityStatus,
    EntityType,
    FilterOutcome,
    MatchDecision,
    NormalizedRef,
    P204ResultArtifact,
    P205ResultArtifact,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
    ProcessingInputManifest,
    ProcessingVersionBundle,
    RawObservationRef,
    RequirementQualificationSet,
    RequirementQualificationState,
)
from careerops_processing.executor import (
    P204StageResult,
    P205StageResult,
    P206Executor,
    P207Executor,
)
from careerops_processing.queue import ProcessingJobLeaseLost
from careerops_processing.worker import ProcessingExecutionDisposition

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64


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
            observed_at=datetime(2026, 9, 10, 10, tzinfo=UTC),
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


def _manifest(*, calibrated: bool) -> ProcessingInputManifest:
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
        target_policy=policy(calibrated=calibrated),
        versions=ProcessingVersionBundle(
            pipeline_version="processing-v2-p207",
            dictionary_version="dict-v1",
            filter_version="filter-v1",
            requirement_extraction_version="requirements-v2",
            evidence_version="evidence-v2",
            qualification_version="qualification-v1",
            scoring_version="scoring-v1",
            calibration_version="gold-v1" if calibrated else "calibration-unset",
            jina=jina(),
        ),
        as_of=datetime(2026, 9, 10, 12, tzinfo=UTC),
    )


def _p204_stage(
    manifest: ProcessingInputManifest,
    *,
    outcome: FilterOutcome,
) -> P204StageResult:
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
        filter_trace_ref=_artifact(
            ProcessingArtifactKind.FILTER_TRACE,
            "careerops.processing.filter-trace.v1",
            HASH_C,
        ),
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


def _p205_stage(
    manifest: ProcessingInputManifest,
    *,
    outcome: FilterOutcome,
    candidate_ref: ProcessingArtifactRef | None,
) -> P205StageResult:
    p204 = _p204_stage(manifest, outcome=outcome)
    return P205StageResult(
        manifest=manifest,
        result=P205ResultArtifact(
            input_fingerprint=manifest.input_fingerprint(),
            manifest=manifest,
            filter_outcome=outcome,
            p2_04_result_ref=p204.result_ref,
            evidence_candidate_set_ref=candidate_ref,
        ),
        result_ref=_artifact(
            ProcessingArtifactKind.P2_05_RESULT,
            "careerops.processing.p2-05-result.v1",
            HASH_E,
        ),
        p204_stage=p204,
    )


class _P205:
    def __init__(self, stage: P205StageResult) -> None:
        self.stage = stage

    async def run_stage(self, _job: Any) -> P205StageResult:
        return self.stage


class _Loader:
    def __init__(self, candidates: Any) -> None:
        self.requirements = requirement_set(
            requirement("req-python", "Python", statement="Python обязателен")
        )
        self.evidence = evidence_set(evidence("ev-python", "Python"))
        self.candidates = candidates

    async def load_requirement_set(self, _ref: ProcessingArtifactRef):
        return self.requirements

    async def load_resume_evidence_set(self, _ref: ProcessingArtifactRef):
        return self.evidence

    async def load_evidence_candidate_set(self, _ref: ProcessingArtifactRef):
        return self.candidates

    async def load_requirement_qualification_set(
        self,
        _ref: ProcessingArtifactRef,
    ) -> RequirementQualificationSet:
        raise AssertionError("P2-07 reuses P2-06 qualification")


class _Publisher:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.qualification: RequirementQualificationSet | None = None
        self.p206_result: Any = None
        self.decision: Any = None
        self.p207_result: Any = None

    def _ref(
        self,
        event: str,
        kind: ProcessingArtifactKind,
        schema_version: str,
        digest: str,
    ) -> ProcessingArtifactRef:
        self.events.append(event)
        return _artifact(kind, schema_version, digest)

    async def publish_requirement_qualification_set(
        self,
        value: RequirementQualificationSet,
    ) -> ProcessingArtifactRef:
        self.qualification = value
        return self._ref(
            "qualification_artifact",
            ProcessingArtifactKind.REQUIREMENT_QUALIFICATION_SET,
            "careerops.processing.requirement-qualification-set.v1",
            HASH_C,
        )

    async def publish_p206_result(self, value: Any) -> ProcessingArtifactRef:
        self.p206_result = value
        return self._ref(
            "p206_result",
            ProcessingArtifactKind.P2_06_RESULT,
            "careerops.processing.p2-06-result.v1",
            HASH_D,
        )

    async def publish_match_decision(self, value: Any) -> ProcessingArtifactRef:
        self.decision = value
        return self._ref(
            "match_decision",
            ProcessingArtifactKind.MATCH_DECISION,
            "careerops.processing.match-decision.v1",
            HASH_E,
        )

    async def publish_p207_result(self, value: Any) -> ProcessingArtifactRef:
        self.p207_result = value
        return self._ref(
            "p207_result",
            ProcessingArtifactKind.P2_07_RESULT,
            "careerops.processing.p2-07-result.v1",
            HASH_F,
        )


class _CurrentPublisher:
    def __init__(self, events: list[str], *, fail_lease: bool = False) -> None:
        self.events = events
        self.fail_lease = fail_lease
        self.decision: Any = None
        self.candidate_ttl_seconds: int | None = None

    async def publish_current(
        self,
        _job: Any,
        *,
        decision: Any,
        result_ref: ProcessingArtifactRef,
        candidate_ttl_seconds: int | None,
    ) -> None:
        del result_ref
        self.events.append("current_publication")
        self.decision = decision
        self.candidate_ttl_seconds = candidate_ttl_seconds
        if self.fail_lease:
            raise ProcessingJobLeaseLost("stale worker")


def _build_executor(
    *,
    calibrated: bool,
    outcome: FilterOutcome = FilterOutcome.KEEP,
    candidate_fingerprint: str | None = None,
    fail_current_lease: bool = False,
):
    manifest = _manifest(calibrated=calibrated)
    p204 = _p204_stage(manifest, outcome=outcome)
    candidate_ref = None
    candidates: Any = object()
    if outcome is FilterOutcome.KEEP:
        requirement_ref = p204.result.requirement_set_ref
        evidence_ref = p204.result.resume_evidence_set_ref
        assert requirement_ref is not None
        assert evidence_ref is not None
        candidate_ref = _artifact(
            ProcessingArtifactKind.EVIDENCE_CANDIDATE_SET,
            "careerops.processing.evidence-candidate-set.v1",
            HASH_F,
        )
        requirements = requirement_set(
            requirement("req-python", "Python", statement="Python обязателен")
        )
        resume_evidence = evidence_set(evidence("ev-python", "Python"))
        candidates = candidate_set(
            requirements,
            resume_evidence,
            scores={"ev-python": 0.01},
        ).model_copy(
            update={
                "input_fingerprint": candidate_fingerprint or manifest.input_fingerprint(),
                "jina": manifest.versions.jina,
            }
        )

    events: list[str] = []
    publisher = _Publisher(events)
    loader = _Loader(candidates)
    p206 = P206Executor(
        p205=_P205(
            _p205_stage(manifest, outcome=outcome, candidate_ref=candidate_ref)
        ),  # type: ignore[arg-type]
        artifact_loader=loader,
        publisher=publisher,  # type: ignore[arg-type]
    )
    current = _CurrentPublisher(events, fail_lease=fail_current_lease)
    p207 = P207Executor(
        p206=p206,
        artifact_loader=loader,
        publisher=publisher,  # type: ignore[arg-type]
        current_publisher=current,  # type: ignore[arg-type]
    )
    return p206, p207, publisher, current, events


@pytest.mark.asyncio
async def test_p206_keep_publishes_selected_evidence_qualification() -> None:
    p206, _, publisher, _, events = _build_executor(calibrated=False)

    result = await p206.execute(object())  # type: ignore[arg-type]

    assert result.disposition is ProcessingExecutionDisposition.SUCCEEDED
    assert publisher.qualification is not None
    evaluation = publisher.qualification.evaluations[0]
    assert evaluation.state is RequirementQualificationState.MATCHED
    assert evaluation.ranked_evidence_ids == ("ev-python",)
    assert evaluation.supporting_evidence_ids == ("ev-python",)
    assert publisher.p206_result.filter_outcome is FilterOutcome.KEEP
    assert events == ["qualification_artifact", "p206_result"]


@pytest.mark.asyncio
async def test_p206_candidate_identity_mismatch_is_terminal_without_publication() -> None:
    p206, _, _, _, events = _build_executor(
        calibrated=False,
        candidate_fingerprint=HASH_A,
    )

    result = await p206.execute(object())  # type: ignore[arg-type]

    assert result.disposition is ProcessingExecutionDisposition.TERMINAL_FAILURE
    assert result.error_category == "processing.p206.input_identity_mismatch"
    assert events == []


@pytest.mark.asyncio
async def test_p207_uncalibrated_keep_is_review_and_artifact_first() -> None:
    _, p207, publisher, current, events = _build_executor(calibrated=False)

    result = await p207.execute(object())  # type: ignore[arg-type]

    assert result.disposition is ProcessingExecutionDisposition.SUCCEEDED
    assert publisher.decision.decision is MatchDecision.REVIEW
    assert current.decision.decision is MatchDecision.REVIEW
    assert current.candidate_ttl_seconds is None
    assert events == [
        "qualification_artifact",
        "p206_result",
        "match_decision",
        "p207_result",
        "current_publication",
    ]


@pytest.mark.asyncio
async def test_p207_calibrated_match_publishes_application_candidate_ttl() -> None:
    _, p207, publisher, current, events = _build_executor(calibrated=True)

    result = await p207.execute(object())  # type: ignore[arg-type]

    assert result.disposition is ProcessingExecutionDisposition.SUCCEEDED
    assert publisher.decision.decision is MatchDecision.APPLICATION_CANDIDATE
    assert current.decision.decision is MatchDecision.APPLICATION_CANDIDATE
    assert current.candidate_ttl_seconds == 3600
    assert events[-3:] == ["match_decision", "p207_result", "current_publication"]


@pytest.mark.asyncio
async def test_p207_exclude_proven_skips_qualification_and_publishes_skip() -> None:
    _, p207, publisher, current, events = _build_executor(
        calibrated=False,
        outcome=FilterOutcome.EXCLUDE_PROVEN,
    )

    result = await p207.execute(object())  # type: ignore[arg-type]

    assert result.disposition is ProcessingExecutionDisposition.SUCCEEDED
    assert publisher.qualification is None
    assert publisher.decision.decision is MatchDecision.SKIP
    assert publisher.decision.requirement_qualification_set_sha256 is None
    assert current.decision.decision is MatchDecision.SKIP
    assert events == [
        "p206_result",
        "match_decision",
        "p207_result",
        "current_publication",
    ]


@pytest.mark.asyncio
async def test_p207_stale_current_publication_leaves_immutable_artifacts_first() -> None:
    _, p207, _, _, events = _build_executor(
        calibrated=True,
        fail_current_lease=True,
    )

    with pytest.raises(ProcessingJobLeaseLost, match="stale worker"):
        await p207.execute(object())  # type: ignore[arg-type]

    assert events == [
        "qualification_artifact",
        "p206_result",
        "match_decision",
        "p207_result",
        "current_publication",
    ]
