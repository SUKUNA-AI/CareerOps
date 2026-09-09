"""Типизированная загрузка неизменяемых Processing artifacts из S3"""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel

from careerops_processing.contracts import (
    EVIDENCE_CANDIDATE_SET_SCHEMA_VERSION,
    REQUIREMENT_SET_SCHEMA_VERSION,
    RESUME_EVIDENCE_SET_SCHEMA_VERSION,
    EvidenceCandidateSet,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
    RequirementSet,
    ResumeEvidenceSet,
)

from .artifacts import (
    ProcessingArtifactIntegrityError,
    ProcessingArtifactStore,
)

T = TypeVar("T", bound=BaseModel)


class ProcessingArtifactLoader:
    """Проверяет artifact ref и восстанавливает typed contract через JSON boundary"""

    def __init__(self, store: ProcessingArtifactStore) -> None:
        self._store = store

    @staticmethod
    def _validate_ref(
        ref: ProcessingArtifactRef,
        *,
        expected_kind: ProcessingArtifactKind,
        expected_schema_version: str,
    ) -> None:
        if ref.kind is not expected_kind:
            raise ValueError("artifact ref имеет неверный kind")
        if ref.schema_version != expected_schema_version:
            raise ValueError("artifact ref имеет неподдерживаемую schema version")

    async def _load(
        self,
        ref: ProcessingArtifactRef,
        *,
        expected_kind: ProcessingArtifactKind,
        expected_schema_version: str,
        model: type[T],
    ) -> T:
        self._validate_ref(
            ref,
            expected_kind=expected_kind,
            expected_schema_version=expected_schema_version,
        )
        try:
            payload = await self._store.get_contract_json(ref)
        except ProcessingArtifactIntegrityError as exc:
            raise ValueError("artifact нарушает content-addressed integrity") from exc
        rendered = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return model.model_validate_json(rendered)

    async def load_requirement_set(self, ref: ProcessingArtifactRef) -> RequirementSet:
        return await self._load(
            ref,
            expected_kind=ProcessingArtifactKind.REQUIREMENT_SET,
            expected_schema_version=REQUIREMENT_SET_SCHEMA_VERSION,
            model=RequirementSet,
        )

    async def load_resume_evidence_set(
        self,
        ref: ProcessingArtifactRef,
    ) -> ResumeEvidenceSet:
        return await self._load(
            ref,
            expected_kind=ProcessingArtifactKind.RESUME_EVIDENCE_SET,
            expected_schema_version=RESUME_EVIDENCE_SET_SCHEMA_VERSION,
            model=ResumeEvidenceSet,
        )

    async def load_evidence_candidate_set(
        self,
        ref: ProcessingArtifactRef,
    ) -> EvidenceCandidateSet:
        return await self._load(
            ref,
            expected_kind=ProcessingArtifactKind.EVIDENCE_CANDIDATE_SET,
            expected_schema_version=EVIDENCE_CANDIDATE_SET_SCHEMA_VERSION,
            model=EvidenceCandidateSet,
        )
