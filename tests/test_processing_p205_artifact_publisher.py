from __future__ import annotations

from typing import Any, cast

import pytest

from careerops_processing.contracts import (
    EvidenceCandidateSet,
    P205ResultArtifact,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
)
from careerops_processing.contracts.artifacts import P205_RESULT_SCHEMA_VERSION
from careerops_processing.contracts.reranking import (
    EVIDENCE_CANDIDATE_SET_SCHEMA_VERSION,
)
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


def _candidate_set() -> EvidenceCandidateSet:
    return EvidenceCandidateSet.model_construct()


def _p205_result() -> P205ResultArtifact:
    return P205ResultArtifact.model_construct()


@pytest.mark.asyncio
async def test_candidate_publication_maps_integrity_failure_to_value_error() -> None:
    publisher, store = _publisher(ProcessingArtifactIntegrityError("corrupt object"))

    with pytest.raises(ValueError, match="EvidenceCandidateSet.*content-addressed integrity"):
        await publisher.publish_evidence_candidate_set(_candidate_set())

    assert store.calls == [
        (ProcessingArtifactKind.EVIDENCE_CANDIDATE_SET, EVIDENCE_CANDIDATE_SET_SCHEMA_VERSION)
    ]


@pytest.mark.asyncio
async def test_p205_result_publication_maps_integrity_failure_to_value_error() -> None:
    publisher, store = _publisher(ProcessingArtifactIntegrityError("corrupt object"))

    with pytest.raises(ValueError, match="P205ResultArtifact.*content-addressed integrity"):
        await publisher.publish_p205_result(_p205_result())

    assert store.calls == [(ProcessingArtifactKind.P2_05_RESULT, P205_RESULT_SCHEMA_VERSION)]


@pytest.mark.asyncio
async def test_p205_publication_does_not_reclassify_transport_failure() -> None:
    transport_failure = ConnectionError("S3 unavailable")
    publisher, store = _publisher(transport_failure)

    with pytest.raises(ConnectionError, match="S3 unavailable") as raised:
        await publisher.publish_evidence_candidate_set(_candidate_set())

    assert raised.value is transport_failure
    assert store.calls == [
        (ProcessingArtifactKind.EVIDENCE_CANDIDATE_SET, EVIDENCE_CANDIDATE_SET_SCHEMA_VERSION)
    ]


@pytest.mark.asyncio
async def test_p205_publication_success_returns_verified_store_reference() -> None:
    publisher, store = _publisher()

    candidate_ref = await publisher.publish_evidence_candidate_set(_candidate_set())
    result_ref = await publisher.publish_p205_result(_p205_result())

    assert candidate_ref.kind is ProcessingArtifactKind.EVIDENCE_CANDIDATE_SET
    assert candidate_ref.schema_version == EVIDENCE_CANDIDATE_SET_SCHEMA_VERSION
    assert result_ref.kind is ProcessingArtifactKind.P2_05_RESULT
    assert result_ref.schema_version == P205_RESULT_SCHEMA_VERSION
    assert store.calls == [
        (ProcessingArtifactKind.EVIDENCE_CANDIDATE_SET, EVIDENCE_CANDIDATE_SET_SCHEMA_VERSION),
        (ProcessingArtifactKind.P2_05_RESULT, P205_RESULT_SCHEMA_VERSION),
    ]
