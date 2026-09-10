from __future__ import annotations

from typing import Any, cast

import pytest

from careerops_processing.contracts import (
    MatchDecisionBundle,
    P206ResultArtifact,
    P207ResultArtifact,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
    RequirementQualificationSet,
)
from careerops_processing.contracts.artifacts import (
    P206_RESULT_SCHEMA_VERSION,
    P207_RESULT_SCHEMA_VERSION,
)
from careerops_processing.contracts.qualification import (
    REQUIREMENT_QUALIFICATION_SET_SCHEMA_VERSION,
)
from careerops_processing.contracts.scoring import MATCH_DECISION_SCHEMA_VERSION
from careerops_processing.infrastructure.artifact_publisher import ProcessingArtifactPublisher
from careerops_processing.infrastructure.artifacts import (
    ProcessingArtifactIntegrityError,
    ProcessingArtifactStore,
)

HASH_A = "a" * 64


class _Store:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[ProcessingArtifactKind, str]] = []

    async def put_contract(
        self,
        *,
        kind: ProcessingArtifactKind,
        schema_version: str,
        payload: Any,
    ) -> ProcessingArtifactRef:
        del payload
        self.calls.append((kind, schema_version))
        if self.failure is not None:
            raise self.failure
        return ProcessingArtifactRef(
            kind=kind,
            schema_version=schema_version,
            uri=f"s3://careerops-artifacts/processing/{kind.value}/{HASH_A}.json",
            sha256=HASH_A,
            size_bytes=10,
        )


def _publisher(failure: Exception | None = None) -> tuple[ProcessingArtifactPublisher, _Store]:
    store = _Store(failure)
    return ProcessingArtifactPublisher(cast(ProcessingArtifactStore, store)), store


def _qualification_set() -> RequirementQualificationSet:
    return RequirementQualificationSet.model_construct()


def _p206_result() -> P206ResultArtifact:
    return P206ResultArtifact.model_construct()


def _match_decision() -> MatchDecisionBundle:
    return MatchDecisionBundle.model_construct()


def _p207_result() -> P207ResultArtifact:
    return P207ResultArtifact.model_construct()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "payload", "kind", "schema_version", "label"),
    [
        (
            "publish_requirement_qualification_set",
            _qualification_set(),
            ProcessingArtifactKind.REQUIREMENT_QUALIFICATION_SET,
            REQUIREMENT_QUALIFICATION_SET_SCHEMA_VERSION,
            "RequirementQualificationSet",
        ),
        (
            "publish_p206_result",
            _p206_result(),
            ProcessingArtifactKind.P2_06_RESULT,
            P206_RESULT_SCHEMA_VERSION,
            "P206ResultArtifact",
        ),
        (
            "publish_match_decision",
            _match_decision(),
            ProcessingArtifactKind.MATCH_DECISION,
            MATCH_DECISION_SCHEMA_VERSION,
            "MatchDecisionBundle",
        ),
        (
            "publish_p207_result",
            _p207_result(),
            ProcessingArtifactKind.P2_07_RESULT,
            P207_RESULT_SCHEMA_VERSION,
            "P207ResultArtifact",
        ),
    ],
)
async def test_p206_p207_integrity_failure_is_deterministic_value_error(
    method_name: str,
    payload: object,
    kind: ProcessingArtifactKind,
    schema_version: str,
    label: str,
) -> None:
    publisher, store = _publisher(ProcessingArtifactIntegrityError("corrupt object"))
    method = getattr(publisher, method_name)

    with pytest.raises(ValueError, match=rf"{label}.*content-addressed integrity"):
        await method(payload)

    assert store.calls == [(kind, schema_version)]


@pytest.mark.asyncio
async def test_p206_p207_transport_failure_is_not_reclassified() -> None:
    failure = ConnectionError("S3 unavailable")
    publisher, store = _publisher(failure)

    with pytest.raises(ConnectionError, match="S3 unavailable") as raised:
        await publisher.publish_match_decision(_match_decision())

    assert raised.value is failure
    assert store.calls == [
        (ProcessingArtifactKind.MATCH_DECISION, MATCH_DECISION_SCHEMA_VERSION)
    ]


@pytest.mark.asyncio
async def test_p206_p207_success_returns_verified_store_references() -> None:
    publisher, store = _publisher()

    qualification_ref = await publisher.publish_requirement_qualification_set(
        _qualification_set()
    )
    p206_ref = await publisher.publish_p206_result(_p206_result())
    decision_ref = await publisher.publish_match_decision(_match_decision())
    p207_ref = await publisher.publish_p207_result(_p207_result())

    assert qualification_ref.kind is ProcessingArtifactKind.REQUIREMENT_QUALIFICATION_SET
    assert qualification_ref.schema_version == REQUIREMENT_QUALIFICATION_SET_SCHEMA_VERSION
    assert p206_ref.kind is ProcessingArtifactKind.P2_06_RESULT
    assert p206_ref.schema_version == P206_RESULT_SCHEMA_VERSION
    assert decision_ref.kind is ProcessingArtifactKind.MATCH_DECISION
    assert decision_ref.schema_version == MATCH_DECISION_SCHEMA_VERSION
    assert p207_ref.kind is ProcessingArtifactKind.P2_07_RESULT
    assert p207_ref.schema_version == P207_RESULT_SCHEMA_VERSION
    assert store.calls == [
        (
            ProcessingArtifactKind.REQUIREMENT_QUALIFICATION_SET,
            REQUIREMENT_QUALIFICATION_SET_SCHEMA_VERSION,
        ),
        (ProcessingArtifactKind.P2_06_RESULT, P206_RESULT_SCHEMA_VERSION),
        (ProcessingArtifactKind.MATCH_DECISION, MATCH_DECISION_SCHEMA_VERSION),
        (ProcessingArtifactKind.P2_07_RESULT, P207_RESULT_SCHEMA_VERSION),
    ]
