"""Публикация артефактов Processing в хранилище с адресацией по содержимому"""

from __future__ import annotations

from careerops_processing.contracts.artifacts import (
    FILTER_TRACE_SCHEMA_VERSION,
    P204_RESULT_SCHEMA_VERSION,
    P205_RESULT_SCHEMA_VERSION,
    P206_RESULT_SCHEMA_VERSION,
    P207_RESULT_SCHEMA_VERSION,
    FilterTraceArtifact,
    P204ResultArtifact,
    P205ResultArtifact,
    P206ResultArtifact,
    P207ResultArtifact,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
)
from careerops_processing.contracts.evidence import (
    RESUME_EVIDENCE_SET_SCHEMA_VERSION,
    ResumeEvidenceSet,
)
from careerops_processing.contracts.filtering import FilterDecision
from careerops_processing.contracts.manifest import (
    MANIFEST_SCHEMA_VERSION,
    ProcessingInputManifest,
)
from careerops_processing.contracts.qualification import (
    REQUIREMENT_QUALIFICATION_SET_SCHEMA_VERSION,
    RequirementQualificationSet,
)
from careerops_processing.contracts.requirements import (
    REQUIREMENT_SET_SCHEMA_VERSION,
    RequirementSet,
)
from careerops_processing.contracts.reranking import (
    EVIDENCE_CANDIDATE_SET_SCHEMA_VERSION,
    EvidenceCandidateSet,
)
from careerops_processing.contracts.scoring import (
    MATCH_DECISION_SCHEMA_VERSION,
    MatchDecisionBundle,
)
from careerops_processing.semantic_cache import SemanticArtifactIntegrityError

from .artifacts import ProcessingArtifactIntegrityError, ProcessingArtifactStore


class ProcessingArtifactPublisher:
    """Публикует и проверяет неизменяемые артефакты Processing"""

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
        try:
            return await self.store.put_contract(
                kind=ProcessingArtifactKind.FILTER_TRACE,
                schema_version=FILTER_TRACE_SCHEMA_VERSION,
                payload=trace,
            )
        except ProcessingArtifactIntegrityError as exc:
            raise ValueError("FilterTraceArtifact нарушает content-addressed integrity") from exc

    async def publish_requirement_set(
        self,
        requirement_set: RequirementSet,
    ) -> ProcessingArtifactRef:
        try:
            return await self.store.put_contract(
                kind=ProcessingArtifactKind.REQUIREMENT_SET,
                schema_version=REQUIREMENT_SET_SCHEMA_VERSION,
                payload=requirement_set,
            )
        except ProcessingArtifactIntegrityError as exc:
            raise SemanticArtifactIntegrityError(
                "RequirementSet нарушает целостность адресуемого по содержимому объекта"
            ) from exc

    async def publish_resume_evidence_set(
        self,
        evidence_set: ResumeEvidenceSet,
    ) -> ProcessingArtifactRef:
        try:
            return await self.store.put_contract(
                kind=ProcessingArtifactKind.RESUME_EVIDENCE_SET,
                schema_version=RESUME_EVIDENCE_SET_SCHEMA_VERSION,
                payload=evidence_set,
            )
        except ProcessingArtifactIntegrityError as exc:
            raise SemanticArtifactIntegrityError(
                "ResumeEvidenceSet нарушает целостность адресуемого по содержимому объекта"
            ) from exc

    async def publish_p204_result(
        self,
        result: P204ResultArtifact,
    ) -> ProcessingArtifactRef:
        try:
            return await self.store.put_contract(
                kind=ProcessingArtifactKind.P2_04_RESULT,
                schema_version=P204_RESULT_SCHEMA_VERSION,
                payload=result,
            )
        except ProcessingArtifactIntegrityError as exc:
            raise ValueError("P204ResultArtifact нарушает content-addressed integrity") from exc

    async def publish_evidence_candidate_set(
        self,
        candidate_set: EvidenceCandidateSet,
    ) -> ProcessingArtifactRef:
        try:
            return await self.store.put_contract(
                kind=ProcessingArtifactKind.EVIDENCE_CANDIDATE_SET,
                schema_version=EVIDENCE_CANDIDATE_SET_SCHEMA_VERSION,
                payload=candidate_set,
            )
        except ProcessingArtifactIntegrityError as exc:
            raise ValueError("EvidenceCandidateSet нарушает content-addressed integrity") from exc

    async def publish_p205_result(
        self,
        result: P205ResultArtifact,
    ) -> ProcessingArtifactRef:
        try:
            return await self.store.put_contract(
                kind=ProcessingArtifactKind.P2_05_RESULT,
                schema_version=P205_RESULT_SCHEMA_VERSION,
                payload=result,
            )
        except ProcessingArtifactIntegrityError as exc:
            raise ValueError("P205ResultArtifact нарушает content-addressed integrity") from exc

    async def publish_requirement_qualification_set(
        self,
        qualification_set: RequirementQualificationSet,
    ) -> ProcessingArtifactRef:
        try:
            return await self.store.put_contract(
                kind=ProcessingArtifactKind.REQUIREMENT_QUALIFICATION_SET,
                schema_version=REQUIREMENT_QUALIFICATION_SET_SCHEMA_VERSION,
                payload=qualification_set,
            )
        except ProcessingArtifactIntegrityError as exc:
            raise ValueError(
                "RequirementQualificationSet нарушает content-addressed integrity"
            ) from exc

    async def publish_p206_result(
        self,
        result: P206ResultArtifact,
    ) -> ProcessingArtifactRef:
        try:
            return await self.store.put_contract(
                kind=ProcessingArtifactKind.P2_06_RESULT,
                schema_version=P206_RESULT_SCHEMA_VERSION,
                payload=result,
            )
        except ProcessingArtifactIntegrityError as exc:
            raise ValueError("P206ResultArtifact нарушает content-addressed integrity") from exc

    async def publish_match_decision(
        self,
        decision: MatchDecisionBundle,
    ) -> ProcessingArtifactRef:
        try:
            return await self.store.put_contract(
                kind=ProcessingArtifactKind.MATCH_DECISION,
                schema_version=MATCH_DECISION_SCHEMA_VERSION,
                payload=decision,
            )
        except ProcessingArtifactIntegrityError as exc:
            raise ValueError("MatchDecisionBundle нарушает content-addressed integrity") from exc

    async def publish_p207_result(
        self,
        result: P207ResultArtifact,
    ) -> ProcessingArtifactRef:
        try:
            return await self.store.put_contract(
                kind=ProcessingArtifactKind.P2_07_RESULT,
                schema_version=P207_RESULT_SCHEMA_VERSION,
                payload=result,
            )
        except ProcessingArtifactIntegrityError as exc:
            raise ValueError("P207ResultArtifact нарушает content-addressed integrity") from exc

    async def verify_artifact(self, ref: ProcessingArtifactRef) -> None:
        """Проверяет существование и целостность артефакта по его ссылке"""

        try:
            await self.store.get_contract_json(ref)
        except ProcessingArtifactIntegrityError as exc:
            raise SemanticArtifactIntegrityError(
                "сохранённый семантический артефакт нарушает целостность"
            ) from exc
