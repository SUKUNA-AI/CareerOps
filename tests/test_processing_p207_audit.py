from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from support.processing import (
    HASH_A,
    HASH_B,
    evidence,
    evidence_set,
    jina,
    policy,
    qualify,
    requirement,
    requirement_set,
)

from careerops_processing.calibration.models import P207PolicyCandidate
from careerops_processing.contracts import (
    BindingSnapshot,
    DataQualityStatus,
    EntityType,
    FilterOutcome,
    MatchDecision,
    MatchDecisionBundle,
    NormalizedRef,
    NormalizedVacancy,
    P204ResultArtifact,
    P205ResultArtifact,
    P206ResultArtifact,
    P207ResultArtifact,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
    ProcessingInputManifest,
    ProcessingVersionBundle,
    RawObservationRef,
    ScoringPolicy,
    SourceValue,
    ValueState,
)
from careerops_processing.core.scoring import score_match
from careerops_processing.replay import verify_p207_replay

HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64
HASH_1 = "1" * 64
HASH_2 = "2" * 64
HASH_3 = "3" * 64
HASH_4 = "4" * 64


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


def _manifest() -> ProcessingInputManifest:
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
        target_policy=policy(calibrated=True),
        versions=ProcessingVersionBundle(
            pipeline_version="processing-v2-p207",
            dictionary_version="dict-v1",
            filter_version="filter-v1",
            requirement_extraction_version="requirements-v2",
            evidence_version="evidence-v2",
            qualification_version="qualification-v1",
            scoring_version="scoring-v1",
            calibration_version="gold-v1",
            jina=jina(),
        ),
        as_of=datetime(2026, 9, 10, 12, tzinfo=UTC),
    )


def _vacancy() -> NormalizedVacancy:
    missing = SourceValue(state=ValueState.NOT_PROVIDED)
    return NormalizedVacancy.model_construct(
        title=missing,
        work_formats=(),
        location=missing,
    )


class _ArtifactReader:
    def __init__(
        self,
        *,
        p204: P204ResultArtifact,
        p205: P205ResultArtifact,
        p206: P206ResultArtifact,
        p207: P207ResultArtifact,
        decision: MatchDecisionBundle,
        requirements,
        qualification,
    ) -> None:
        self.p204 = p204
        self.p205 = p205
        self.p206 = p206
        self.p207 = p207
        self.decision = decision
        self.requirements = requirements
        self.qualification = qualification

    async def load_p207_result(self, _ref: ProcessingArtifactRef) -> P207ResultArtifact:
        return self.p207

    async def load_p206_result(self, _ref: ProcessingArtifactRef) -> P206ResultArtifact:
        return self.p206

    async def load_p205_result(self, _ref: ProcessingArtifactRef) -> P205ResultArtifact:
        return self.p205

    async def load_p204_result(self, _ref: ProcessingArtifactRef) -> P204ResultArtifact:
        return self.p204

    async def load_match_decision(self, _ref: ProcessingArtifactRef) -> MatchDecisionBundle:
        return self.decision

    async def load_requirement_set(self, _ref: ProcessingArtifactRef):
        return self.requirements

    async def load_requirement_qualification_set(self, _ref: ProcessingArtifactRef):
        return self.qualification


class _InputReader:
    def __init__(self, vacancy: NormalizedVacancy) -> None:
        self.vacancy = vacancy

    async def load_vacancy(self, _ref: NormalizedRef) -> NormalizedVacancy:
        return self.vacancy


def _replay_fixture(*, wrong_decision: bool = False):
    manifest = _manifest()
    fingerprint = manifest.input_fingerprint()
    requirements = requirement_set(
        requirement("req-python", "Python", statement="Python обязателен")
    )
    qualification = qualify(
        requirements,
        evidence_set(evidence("ev-python", "Python")),
    ).model_copy(
        update={
            "input_fingerprint": fingerprint,
            "requirement_set_sha256": HASH_A,
            "qualification_version": manifest.versions.qualification_version,
        }
    )
    vacancy = _vacancy()

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
    filter_ref = _artifact(
        ProcessingArtifactKind.FILTER_TRACE,
        "careerops.processing.filter-trace.v1",
        HASH_C,
    )
    p204_ref = _artifact(
        ProcessingArtifactKind.P2_04_RESULT,
        "careerops.processing.p2-04-result.v2",
        HASH_D,
    )
    candidate_ref = _artifact(
        ProcessingArtifactKind.EVIDENCE_CANDIDATE_SET,
        "careerops.processing.evidence-candidate-set.v1",
        HASH_E,
    )
    p205_ref = _artifact(
        ProcessingArtifactKind.P2_05_RESULT,
        "careerops.processing.p2-05-result.v1",
        HASH_F,
    )
    qualification_ref = _artifact(
        ProcessingArtifactKind.REQUIREMENT_QUALIFICATION_SET,
        "careerops.processing.requirement-qualification-set.v1",
        HASH_1,
    )
    p206_ref = _artifact(
        ProcessingArtifactKind.P2_06_RESULT,
        "careerops.processing.p2-06-result.v1",
        HASH_2,
    )
    decision_ref = _artifact(
        ProcessingArtifactKind.MATCH_DECISION,
        "careerops.processing.match-decision.v1",
        HASH_3,
    )
    p207_ref = _artifact(
        ProcessingArtifactKind.P2_07_RESULT,
        "careerops.processing.p2-07-result.v1",
        HASH_4,
    )

    p204 = P204ResultArtifact(
        input_fingerprint=fingerprint,
        manifest=manifest,
        filter_outcome=FilterOutcome.KEEP,
        filter_trace_ref=filter_ref,
        requirement_extraction_version=manifest.versions.requirement_extraction_version,
        evidence_version=manifest.versions.evidence_version,
        requirement_set_ref=requirement_ref,
        resume_evidence_set_ref=evidence_ref,
    )
    p205 = P205ResultArtifact(
        input_fingerprint=fingerprint,
        manifest=manifest,
        filter_outcome=FilterOutcome.KEEP,
        p2_04_result_ref=p204_ref,
        evidence_candidate_set_ref=candidate_ref,
    )
    p206 = P206ResultArtifact(
        input_fingerprint=fingerprint,
        manifest=manifest,
        filter_outcome=FilterOutcome.KEEP,
        p2_05_result_ref=p205_ref,
        requirement_qualification_set_ref=qualification_ref,
    )

    decision = score_match(
        input_fingerprint=fingerprint,
        qualification_set_ref_sha256=qualification_ref.sha256,
        requirement_set=requirements,
        qualification_set=qualification,
        target_policy=manifest.target_policy,
        scoring_version=manifest.versions.scoring_version,
        calibration_version=manifest.versions.calibration_version,
        vacancy=vacancy,
    )
    if wrong_decision:
        decision = MatchDecisionBundle.model_validate(
            {
                **decision.model_dump(mode="python"),
                "decision": MatchDecision.REVIEW,
                "reason_codes": ("match.policy_uncertain",),
            }
        )

    p207 = P207ResultArtifact(
        input_fingerprint=fingerprint,
        manifest=manifest,
        filter_outcome=FilterOutcome.KEEP,
        p2_06_result_ref=p206_ref,
        match_decision_ref=decision_ref,
    )
    return (
        p207_ref,
        _ArtifactReader(
            p204=p204,
            p205=p205,
            p206=p206,
            p207=p207,
            decision=decision,
            requirements=requirements,
            qualification=qualification,
        ),
        _InputReader(vacancy),
    )


def test_scoring_policy_rejects_requirement_double_counting() -> None:
    with pytest.raises(ValidationError, match="double-count"):
        ScoringPolicy(
            calibration_version="gold-v1",
            candidate_min_score=Decimal("80"),
            mandatory_min_support=Decimal("1"),
            component_weights={
                "mandatory_coverage": Decimal("1"),
                "technology_fit": Decimal("1"),
            },
            candidate_ttl_seconds=3600,
        )


def test_calibration_candidate_rejects_requirement_double_counting() -> None:
    with pytest.raises(ValidationError, match="double-count"):
        P207PolicyCandidate(
            candidate_id="invalid-overlap",
            candidate_min_score=Decimal("80"),
            mandatory_min_support=Decimal("1"),
            component_weights={
                "mandatory_coverage": Decimal("1"),
                "technology_fit": Decimal("1"),
            },
        )


@pytest.mark.asyncio
async def test_p207_replay_reproduces_stored_decision_from_pinned_chain() -> None:
    result_ref, artifacts, inputs = _replay_fixture()

    replayed = await verify_p207_replay(
        p207_result_ref=result_ref,
        artifact_loader=artifacts,
        input_loader=inputs,
    )

    assert replayed.decision is MatchDecision.APPLICATION_CANDIDATE


@pytest.mark.asyncio
async def test_p207_replay_rejects_non_reproducible_stored_decision() -> None:
    result_ref, artifacts, inputs = _replay_fixture(wrong_decision=True)

    with pytest.raises(ValueError, match="replay mismatch"):
        await verify_p207_replay(
            p207_result_ref=result_ref,
            artifact_loader=artifacts,
            input_loader=inputs,
        )
