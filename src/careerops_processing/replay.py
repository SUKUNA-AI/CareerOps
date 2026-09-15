"""Deterministic P2-07 replay over immutable Processing artifacts."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from careerops_processing.contracts import (
    FilterOutcome,
    MatchDecision,
    MatchDecisionBundle,
    NormalizedRef,
    NormalizedVacancy,
    P204ResultArtifact,
    P205ResultArtifact,
    P206ResultArtifact,
    P207ResultArtifact,
    ProcessingArtifactRef,
    ProcessingInputManifest,
    RequirementQualificationSet,
    RequirementSet,
    ScoreBounds,
)
from careerops_processing.core.scoring import score_match


class P207ReplayArtifactReader(Protocol):
    async def load_p207_result(
        self,
        ref: ProcessingArtifactRef,
    ) -> P207ResultArtifact:
        ...

    async def load_p206_result(
        self,
        ref: ProcessingArtifactRef,
    ) -> P206ResultArtifact:
        ...

    async def load_p205_result(
        self,
        ref: ProcessingArtifactRef,
    ) -> P205ResultArtifact:
        ...

    async def load_p204_result(
        self,
        ref: ProcessingArtifactRef,
    ) -> P204ResultArtifact:
        ...

    async def load_match_decision(
        self,
        ref: ProcessingArtifactRef,
    ) -> MatchDecisionBundle:
        ...

    async def load_requirement_set(
        self,
        ref: ProcessingArtifactRef,
    ) -> RequirementSet:
        ...

    async def load_requirement_qualification_set(
        self,
        ref: ProcessingArtifactRef,
    ) -> RequirementQualificationSet:
        ...


class P207ReplayInputReader(Protocol):
    async def load_vacancy(self, ref: NormalizedRef) -> NormalizedVacancy:
        ...


def _validate_stage_identity(
    stage: P204ResultArtifact | P205ResultArtifact | P206ResultArtifact,
    *,
    manifest: ProcessingInputManifest,
    filter_outcome: FilterOutcome,
) -> None:
    if stage.input_fingerprint != manifest.input_fingerprint():
        raise ValueError("P2-07 replay stage input fingerprint mismatch")
    if stage.manifest != manifest:
        raise ValueError("P2-07 replay stage manifest mismatch")
    if stage.filter_outcome is not filter_outcome:
        raise ValueError("P2-07 replay filter outcome mismatch")


def _excluded_decision(manifest: ProcessingInputManifest) -> MatchDecisionBundle:
    return MatchDecisionBundle(
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


async def verify_p207_replay(
    *,
    p207_result_ref: ProcessingArtifactRef,
    artifact_loader: P207ReplayArtifactReader,
    input_loader: P207ReplayInputReader,
) -> MatchDecisionBundle:
    """Recompute a stored P2-07 decision from its pinned immutable inputs."""

    p207 = await artifact_loader.load_p207_result(p207_result_ref)
    manifest = p207.manifest
    fingerprint = manifest.input_fingerprint()
    if p207.input_fingerprint != fingerprint:
        raise ValueError("P2-07 replay result input fingerprint mismatch")

    stored = await artifact_loader.load_match_decision(p207.match_decision_ref)
    if (
        stored.input_fingerprint != fingerprint
        or stored.scoring_version != manifest.versions.scoring_version
        or stored.calibration_version != manifest.versions.calibration_version
        or stored.policy_version != manifest.target_policy.policy_version
    ):
        raise ValueError("P2-07 replay stored decision identity mismatch")

    p206 = await artifact_loader.load_p206_result(p207.p2_06_result_ref)
    p205 = await artifact_loader.load_p205_result(p206.p2_05_result_ref)
    p204 = await artifact_loader.load_p204_result(p205.p2_04_result_ref)
    for stage in (p206, p205, p204):
        _validate_stage_identity(
            stage,
            manifest=manifest,
            filter_outcome=p207.filter_outcome,
        )

    if p207.filter_outcome is FilterOutcome.EXCLUDE_PROVEN:
        replayed = _excluded_decision(manifest)
    else:
        requirement_ref = p204.requirement_set_ref
        qualification_ref = p206.requirement_qualification_set_ref
        if requirement_ref is None or qualification_ref is None:
            raise ValueError("P2-07 replay KEEP chain is missing semantic artifacts")

        requirements = await artifact_loader.load_requirement_set(requirement_ref)
        qualification = await artifact_loader.load_requirement_qualification_set(
            qualification_ref
        )
        if (
            qualification.input_fingerprint != fingerprint
            or qualification.requirement_set_sha256 != requirement_ref.sha256
            or qualification.qualification_version
            != manifest.versions.qualification_version
        ):
            raise ValueError("P2-07 replay qualification identity mismatch")

        vacancy = await input_loader.load_vacancy(manifest.vacancy)
        replayed = score_match(
            input_fingerprint=fingerprint,
            qualification_set_ref_sha256=qualification_ref.sha256,
            requirement_set=requirements,
            qualification_set=qualification,
            target_policy=manifest.target_policy,
            scoring_version=manifest.versions.scoring_version,
            calibration_version=manifest.versions.calibration_version,
            vacancy=vacancy,
        )

    if replayed != stored:
        raise ValueError("P2-07 replay mismatch with stored MatchDecisionBundle")
    return replayed
