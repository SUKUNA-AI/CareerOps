"""gRPC boundary from Python orchestration to the native P2-06/P2-07 core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import grpc
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Struct

from .contracts import (
    EvidenceCandidateSet,
    MatchDecisionBundle,
    NormalizedVacancy,
    ProcessingInputManifest,
    RequirementQualificationSet,
    RequirementSet,
    ResumeEvidenceSet,
)

DECISION_PROTOCOL_VERSION = "careerops.matching-core.decision.v1"
_EVALUATE_METHOD = "/careerops.matching_core.v1.MatchingCoreDecision/Evaluate"


class MatchingCoreUnavailableError(RuntimeError):
    """The local native decision service could not serve the request."""


class MatchingCoreProtocolError(ValueError):
    """The native service rejected or returned an invalid decision contract."""


@dataclass(frozen=True, slots=True)
class NativeDecisionResult:
    qualification_set: RequirementQualificationSet
    decision: MatchDecisionBundle


class DecisionCore(Protocol):
    async def evaluate(
        self,
        *,
        manifest: ProcessingInputManifest,
        requirement_set_sha256: str,
        resume_evidence_set_sha256: str,
        evidence_candidate_set_sha256: str,
        requirement_set: RequirementSet,
        evidence_set: ResumeEvidenceSet,
        candidate_set: EvidenceCandidateSet,
        vacancy: NormalizedVacancy | None,
    ) -> NativeDecisionResult: ...


class MatchingCoreDecisionClient:
    """Thin async client; all P2-06/P2-07 semantics live in C++."""

    def __init__(self, target: str, *, timeout_seconds: float = 5.0) -> None:
        target = target.strip()
        if not target:
            raise ValueError("matching-core target must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("matching-core timeout must be positive")
        self._target = target
        self._timeout_seconds = timeout_seconds
        self._channel: Any = None

    async def __aenter__(self) -> MatchingCoreDecisionClient:
        self._channel = grpc.aio.insecure_channel(self._target)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._channel is not None:
            await self._channel.close()
            self._channel = None

    async def evaluate(
        self,
        *,
        manifest: ProcessingInputManifest,
        requirement_set_sha256: str,
        resume_evidence_set_sha256: str,
        evidence_candidate_set_sha256: str,
        requirement_set: RequirementSet,
        evidence_set: ResumeEvidenceSet,
        candidate_set: EvidenceCandidateSet,
        vacancy: NormalizedVacancy | None,
    ) -> NativeDecisionResult:
        if self._channel is None:
            raise RuntimeError("matching-core client is not open")

        payload: dict[str, Any] = {
            "protocol_version": DECISION_PROTOCOL_VERSION,
            "input_fingerprint": manifest.input_fingerprint(),
            "requirement_set_sha256": requirement_set_sha256,
            "resume_evidence_set_sha256": resume_evidence_set_sha256,
            "evidence_candidate_set_sha256": evidence_candidate_set_sha256,
            "qualification_version": manifest.versions.qualification_version,
            "scoring_version": manifest.versions.scoring_version,
            "calibration_version": manifest.versions.calibration_version,
            "policy_version": manifest.target_policy.policy_version,
            "as_of": manifest.as_of.date().isoformat(),
            "requirement_set": requirement_set.model_dump(mode="json"),
            "resume_evidence_set": evidence_set.model_dump(mode="json"),
            "evidence_candidate_set": candidate_set.model_dump(mode="json"),
            "target_policy_content": manifest.target_policy.parsed_content(),
            "vacancy": None if vacancy is None else vacancy.model_dump(mode="json"),
        }
        request = ParseDict(payload, Struct())
        call = self._channel.unary_unary(
            _EVALUATE_METHOD,
            request_serializer=Struct.SerializeToString,
            response_deserializer=Struct.FromString,
        )
        try:
            response = await call(request, timeout=self._timeout_seconds)
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.INVALID_ARGUMENT:
                raise MatchingCoreProtocolError(exc.details() or "invalid native request") from exc
            raise MatchingCoreUnavailableError(
                exc.details() or f"matching-core RPC failed: {exc.code().name}"
            ) from exc

        data = MessageToDict(response, preserving_proto_field_name=True)
        if data.get("protocol_version") != DECISION_PROTOCOL_VERSION:
            raise MatchingCoreProtocolError("matching-core decision protocol mismatch")
        try:
            qualification = RequirementQualificationSet.model_validate(data["qualification_set"])
            decision = MatchDecisionBundle.model_validate(data["decision"])
        except (KeyError, ValueError, TypeError) as exc:
            raise MatchingCoreProtocolError("invalid matching-core decision response") from exc
        return NativeDecisionResult(qualification_set=qualification, decision=decision)
