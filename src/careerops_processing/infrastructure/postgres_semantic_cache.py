"""PostgreSQL индекс переиспользуемых семантических артефактов P2-04"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection

from careerops_processing.contracts import ProcessingArtifactKind, ProcessingArtifactRef
from careerops_processing.semantic_cache import (
    SemanticArtifactConflict,
    SemanticArtifactKey,
)


class PostgresSemanticArtifactRegistry:
    """Хранит семантическую идентичность и ссылку на артефакт, сериализуя сборку"""

    def __init__(self, conn: AsyncConnection[Any]) -> None:
        if not conn.autocommit:
            raise ValueError("PostgresSemanticArtifactRegistry требует autocommit connection")
        self._conn = conn

    @staticmethod
    def _advisory_lock_id(key: SemanticArtifactKey) -> int:
        value = int(key.cache_key()[:16], 16)
        if value >= 2**63:
            value -= 2**64
        return value

    @asynccontextmanager
    async def lock(self, key: SemanticArtifactKey) -> AsyncIterator[None]:
        lock_id = self._advisory_lock_id(key)
        await self._conn.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
        try:
            yield
        finally:
            await self._conn.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))

    async def get(self, key: SemanticArtifactKey) -> ProcessingArtifactRef | None:
        cursor = await self._conn.execute(
            """
            SELECT artifact_kind,
                   source_key,
                   source_entity_id,
                   account_key,
                   semantic_content_hash,
                   normalized_schema_version,
                   normalization_version,
                   dictionary_version,
                   semantic_version,
                   artifact_schema_version,
                   artifact_uri,
                   artifact_sha256,
                   artifact_size_bytes
            FROM careerops_v2.processing_semantic_artifacts
            WHERE cache_key = %s
            """,
            (key.cache_key(),),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        stored_identity = (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            None if row[3] is None else str(row[3]),
            str(row[4]),
            str(row[5]),
            str(row[6]),
            str(row[7]),
            str(row[8]),
            str(row[9]),
        )
        expected_identity = (
            key.kind.value,
            key.source_key,
            key.source_entity_id,
            key.account_key,
            key.semantic_content_hash,
            key.normalized_schema_version,
            key.normalization_version,
            key.dictionary_version,
            key.semantic_version,
            key.artifact_schema_version,
        )
        if stored_identity != expected_identity:
            raise SemanticArtifactConflict(
                "cache_key связан с другой семантической идентичностью в PostgreSQL"
            )

        try:
            ref = ProcessingArtifactRef(
                kind=ProcessingArtifactKind(str(row[0])),
                schema_version=str(row[9]),
                uri=str(row[10]),
                sha256=str(row[11]),
                size_bytes=int(row[12]),
            )
        except (TypeError, ValueError) as exc:
            raise SemanticArtifactConflict(
                "PostgreSQL содержит некорректную ссылку на семантический артефакт"
            ) from exc
        self._validate_ref(key, ref)
        return ref

    async def put(
        self,
        key: SemanticArtifactKey,
        ref: ProcessingArtifactRef,
    ) -> ProcessingArtifactRef:
        self._validate_ref(key, ref)
        await self._conn.execute(
            """
            INSERT INTO careerops_v2.processing_semantic_artifacts (
                cache_key,
                artifact_kind,
                source_key,
                source_entity_id,
                account_key,
                semantic_content_hash,
                normalized_schema_version,
                normalization_version,
                dictionary_version,
                semantic_version,
                artifact_schema_version,
                artifact_uri,
                artifact_sha256,
                artifact_size_bytes
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (cache_key) DO NOTHING
            """,
            (
                key.cache_key(),
                key.kind.value,
                key.source_key,
                key.source_entity_id,
                key.account_key,
                key.semantic_content_hash,
                key.normalized_schema_version,
                key.normalization_version,
                key.dictionary_version,
                key.semantic_version,
                key.artifact_schema_version,
                ref.uri,
                ref.sha256,
                ref.size_bytes,
            ),
        )
        stored = await self.get(key)
        if stored is None:
            raise RuntimeError("PostgreSQL не сохранил ссылку на семантический артефакт")
        if stored != ref:
            raise SemanticArtifactConflict(
                "семантическая идентичность уже связана с другим неизменяемым артефактом"
            )
        return stored

    @staticmethod
    def _validate_ref(key: SemanticArtifactKey, ref: ProcessingArtifactRef) -> None:
        if ref.kind is not key.kind:
            raise SemanticArtifactConflict(
                "вид артефакта не совпадает с семантической идентичностью"
            )
        if ref.schema_version != key.artifact_schema_version:
            raise SemanticArtifactConflict(
                "версия схемы артефакта не совпадает с семантической идентичностью"
            )
