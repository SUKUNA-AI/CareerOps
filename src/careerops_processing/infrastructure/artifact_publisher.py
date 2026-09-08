"""Публикация воспроизводимых артефактов Processing в content-addressed хранилище"""

from __future__ import annotations

from careerops_processing.contracts.artifacts import (
    FILTER_TRACE_SCHEMA_VERSION,
    FilterTraceArtifact,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
)
from careerops_processing.contracts.filtering import FilterDecision
from careerops_processing.contracts.manifest import (
    MANIFEST_SCHEMA_VERSION,
    ProcessingInputManifest,
)

from .artifacts import ProcessingArtifactStore


class ProcessingArtifactPublisher:
    """Публикует manifest и filter trace без изменяемых указателей latest"""

    def __init__(self, store: ProcessingArtifactStore) -> None:
        self.store = store

    async def publish_manifest(
        self,
        manifest: ProcessingInputManifest,
    ) -> ProcessingArtifactRef:
        return await self.store.put_contract(
            kind=ProcessingArtifactKind.INPUT_MANIFEST,
            schema_version=MANIFEST_SCHEMA_VERSION,
            payload=manifest,
        )

    async def publish_filter_trace(
        self,
        manifest: ProcessingInputManifest,
        decision: FilterDecision,
    ) -> ProcessingArtifactRef:
        trace = FilterTraceArtifact(
            input_fingerprint=manifest.input_fingerprint(),
            manifest=manifest,
            filter_version=manifest.versions.filter_version,
            decision=decision,
        )
        return await self.store.put_contract(
            kind=ProcessingArtifactKind.FILTER_TRACE,
            schema_version=FILTER_TRACE_SCHEMA_VERSION,
            payload=trace,
        )
