"""Повторное использование артефактов P2-04 по семантической версии"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from .contracts import (
    REQUIREMENT_SET_SCHEMA_VERSION,
    RESUME_EVIDENCE_SET_SCHEMA_VERSION,
    NormalizedResume,
    NormalizedVacancy,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
    RequirementSet,
    ResumeEvidenceSet,
)
from .core import extract_requirements, extract_resume_evidence

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SemanticArtifactResolutionError(ValueError):
    """Детерминированная ошибка получения семантического артефакта P2-04"""


class SemanticArtifactIdentityError(SemanticArtifactResolutionError):
    """Некорректная идентичность переиспользуемого семантического артефакта"""


class SemanticArtifactConflict(SemanticArtifactResolutionError):
    """Одна семантическая идентичность связана с разными артефактами"""


class SemanticArtifactIntegrityError(SemanticArtifactResolutionError):
    """Сохранённый артефакт нарушает ожидаемую адресуемую по содержимому целостность"""


class SemanticArtifactRebuildMismatch(SemanticArtifactIntegrityError):
    """Повторная сборка дала другой адресуемый по содержимому артефакт"""


@dataclass(frozen=True, slots=True)
class SemanticArtifactKey:
    """Полная идентичность одного переиспользуемого семантического артефакта"""

    kind: ProcessingArtifactKind
    source_key: str
    source_entity_id: str
    account_key: str | None
    semantic_content_hash: str
    normalized_schema_version: str
    normalization_version: str
    dictionary_version: str
    semantic_version: str
    artifact_schema_version: str

    def __post_init__(self) -> None:
        expected_schema = {
            ProcessingArtifactKind.REQUIREMENT_SET: REQUIREMENT_SET_SCHEMA_VERSION,
            ProcessingArtifactKind.RESUME_EVIDENCE_SET: RESUME_EVIDENCE_SET_SCHEMA_VERSION,
        }.get(self.kind)
        if expected_schema is None:
            raise SemanticArtifactIdentityError(
                "индекс P2-04 поддерживает только семантические артефакты"
            )
        if self.artifact_schema_version != expected_schema:
            raise SemanticArtifactIdentityError(
                "вид семантического артефакта не совпадает с версией его схемы"
            )
        for name, value in (
            ("source_key", self.source_key),
            ("source_entity_id", self.source_entity_id),
            ("semantic_content_hash", self.semantic_content_hash),
            ("normalized_schema_version", self.normalized_schema_version),
            ("normalization_version", self.normalization_version),
            ("dictionary_version", self.dictionary_version),
            ("semantic_version", self.semantic_version),
            ("artifact_schema_version", self.artifact_schema_version),
        ):
            if not value.strip():
                raise SemanticArtifactIdentityError(f"{name} не должен быть пустым")
        if _SHA256.fullmatch(self.semantic_content_hash) is None:
            raise SemanticArtifactIdentityError(
                "semantic_content_hash должен быть lowercase SHA-256"
            )
        if self.kind is ProcessingArtifactKind.REQUIREMENT_SET and self.account_key is not None:
            raise SemanticArtifactIdentityError(
                "requirement_set не должен содержать account_key в семантической идентичности"
            )
        if self.kind is ProcessingArtifactKind.RESUME_EVIDENCE_SET:
            if self.account_key is None or not self.account_key.strip():
                raise SemanticArtifactIdentityError(
                    "resume_evidence_set требует account_key в семантической идентичности"
                )

    def cache_key(self) -> str:
        payload = {
            "kind": self.kind.value,
            "source_key": self.source_key,
            "source_entity_id": self.source_entity_id,
            "account_key": self.account_key,
            "semantic_content_hash": self.semantic_content_hash,
            "normalized_schema_version": self.normalized_schema_version,
            "normalization_version": self.normalization_version,
            "dictionary_version": self.dictionary_version,
            "semantic_version": self.semantic_version,
            "artifact_schema_version": self.artifact_schema_version,
        }
        rendered = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


class SemanticArtifactRegistry(Protocol):
    """Постоянный индекс и межпроцессная блокировка артефактов P2-04"""

    def lock(self, key: SemanticArtifactKey) -> AbstractAsyncContextManager[None]: ...

    async def get(self, key: SemanticArtifactKey) -> ProcessingArtifactRef | None: ...

    async def put(
        self,
        key: SemanticArtifactKey,
        ref: ProcessingArtifactRef,
    ) -> ProcessingArtifactRef: ...


class P204SemanticPublisher(Protocol):
    """Публикует и проверяет семантические артефакты P2-04"""

    async def publish_requirement_set(
        self,
        requirement_set: RequirementSet,
    ) -> ProcessingArtifactRef: ...

    async def publish_resume_evidence_set(
        self,
        evidence_set: ResumeEvidenceSet,
    ) -> ProcessingArtifactRef: ...

    async def verify_artifact(self, ref: ProcessingArtifactRef) -> None: ...


class P204SemanticArtifactResolver:
    """Переиспользует готовый артефакт или строит его под межпроцессной блокировкой"""

    def __init__(
        self,
        *,
        registry: SemanticArtifactRegistry,
        publisher: P204SemanticPublisher,
    ) -> None:
        self._registry = registry
        self._publisher = publisher

    async def _resolve(
        self,
        key: SemanticArtifactKey,
        build: Callable[[], Awaitable[ProcessingArtifactRef]],
    ) -> ProcessingArtifactRef:
        async with self._registry.lock(key):
            existing = await self._registry.get(key)
            if existing is None:
                created = await build()
                return await self._registry.put(key, created)

            try:
                await self._publisher.verify_artifact(existing)
                return existing
            except FileNotFoundError as exc:
                rebuilt = await build()
                if rebuilt != existing:
                    raise SemanticArtifactRebuildMismatch(
                        "повторная сборка не совпала с сохранённой ссылкой на артефакт"
                    ) from exc
                return rebuilt

    async def resolve_requirements(
        self,
        vacancy: NormalizedVacancy,
        *,
        extraction_version: str,
    ) -> ProcessingArtifactRef:
        key = SemanticArtifactKey(
            kind=ProcessingArtifactKind.REQUIREMENT_SET,
            source_key=vacancy.source_key,
            source_entity_id=vacancy.source_entity_id,
            account_key=None,
            semantic_content_hash=vacancy.semantic_content_hash,
            normalized_schema_version=vacancy.schema_version,
            normalization_version=vacancy.normalization_version,
            dictionary_version=vacancy.dictionary_version,
            semantic_version=extraction_version,
            artifact_schema_version=REQUIREMENT_SET_SCHEMA_VERSION,
        )

        async def build() -> ProcessingArtifactRef:
            try:
                result = extract_requirements(
                    vacancy,
                    extraction_version=extraction_version,
                )
            except ValueError as exc:
                raise SemanticArtifactResolutionError(
                    "RequirementSet не прошёл детерминированную валидацию"
                ) from exc
            return await self._publisher.publish_requirement_set(result)

        return await self._resolve(key, build)

    async def resolve_evidence(
        self,
        resume: NormalizedResume,
        *,
        evidence_version: str,
    ) -> ProcessingArtifactRef:
        key = SemanticArtifactKey(
            kind=ProcessingArtifactKind.RESUME_EVIDENCE_SET,
            source_key=resume.source_key,
            source_entity_id=resume.source_entity_id,
            account_key=resume.account_key,
            semantic_content_hash=resume.semantic_content_hash,
            normalized_schema_version=resume.schema_version,
            normalization_version=resume.normalization_version,
            dictionary_version=resume.dictionary_version,
            semantic_version=evidence_version,
            artifact_schema_version=RESUME_EVIDENCE_SET_SCHEMA_VERSION,
        )

        async def build() -> ProcessingArtifactRef:
            try:
                result = extract_resume_evidence(
                    resume,
                    evidence_version=evidence_version,
                )
            except ValueError as exc:
                raise SemanticArtifactResolutionError(
                    "ResumeEvidenceSet не прошёл детерминированную валидацию"
                ) from exc
            return await self._publisher.publish_resume_evidence_set(result)

        return await self._resolve(key, build)
