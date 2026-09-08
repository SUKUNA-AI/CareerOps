from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
from alembic.config import Config
from support.postgres import PostgresTestTarget

from alembic import command
from careerops_processing.contracts import (
    REQUIREMENT_SET_SCHEMA_VERSION,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
)
from careerops_processing.infrastructure import PostgresSemanticArtifactRegistry
from careerops_processing.semantic_cache import SemanticArtifactKey
from careerops_storage.v2 import processing_semantic_artifacts

pytestmark = pytest.mark.integration_postgres

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


@pytest.fixture
def semantic_registry_target(
    v2_postgres_test_target: PostgresTestTarget,
) -> PostgresTestTarget:
    command.upgrade(_alembic_config(), "head")
    return v2_postgres_test_target


@pytest_asyncio.fixture
async def registry_connections(
    semantic_registry_target: PostgresTestTarget,
) -> AsyncIterator[
    tuple[psycopg.AsyncConnection[object], psycopg.AsyncConnection[object]]
]:
    first = await psycopg.AsyncConnection.connect(
        semantic_registry_target.dsn,
        autocommit=True,
    )
    second = await psycopg.AsyncConnection.connect(
        semantic_registry_target.dsn,
        autocommit=True,
    )
    try:
        yield first, second
    finally:
        await first.close()
        await second.close()


def _key() -> SemanticArtifactKey:
    return SemanticArtifactKey(
        kind=ProcessingArtifactKind.REQUIREMENT_SET,
        source_key="hh",
        source_entity_id="vacancy-42",
        account_key=None,
        semantic_content_hash="a" * 64,
        normalized_schema_version="careerops.hh.vacancy.normalized.v1",
        normalization_version="spark-hh-normalizer-1",
        dictionary_version="careerops-dictionary-2026-09",
        semantic_version="requirements-v2",
        artifact_schema_version=REQUIREMENT_SET_SCHEMA_VERSION,
    )


def _ref() -> ProcessingArtifactRef:
    return ProcessingArtifactRef(
        kind=ProcessingArtifactKind.REQUIREMENT_SET,
        schema_version=REQUIREMENT_SET_SCHEMA_VERSION,
        uri="s3://careerops-artifacts/processing/requirement-set.json",
        sha256="b" * 64,
        size_bytes=123,
    )


@pytest.mark.asyncio
async def test_semantic_registry_migration_matches_sqlalchemy_metadata(
    registry_connections: tuple[
        psycopg.AsyncConnection[object],
        psycopg.AsyncConnection[object],
    ],
) -> None:
    connection, _ = registry_connections

    columns_cursor = await connection.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'careerops_v2'
          AND table_name = 'processing_semantic_artifacts'
        ORDER BY ordinal_position
        """
    )
    database_columns = [str(row[0]) for row in await columns_cursor.fetchall()]
    metadata_columns = [column.name for column in processing_semantic_artifacts.columns]
    assert database_columns == metadata_columns

    constraints_cursor = await connection.execute(
        """
        SELECT constraint_row.conname
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS relation ON relation.oid = constraint_row.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'careerops_v2'
          AND relation.relname = 'processing_semantic_artifacts'
          AND constraint_row.contype IN ('p', 'u', 'f', 'c')
        ORDER BY constraint_row.conname
        """
    )
    database_constraints = {str(row[0]) for row in await constraints_cursor.fetchall()}
    metadata_constraints = {
        str(constraint.name)
        for constraint in processing_semantic_artifacts.constraints
        if constraint.name is not None
    }
    assert database_constraints == metadata_constraints

    indexes_cursor = await connection.execute(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'careerops_v2'
          AND tablename = 'processing_semantic_artifacts'
          AND indexname <> 'pk_processing_semantic_artifacts'
        ORDER BY indexname
        """
    )
    database_indexes = {str(row[0]) for row in await indexes_cursor.fetchall()}
    metadata_indexes = {str(index.name) for index in processing_semantic_artifacts.indexes}
    assert database_indexes == metadata_indexes


@pytest.mark.asyncio
async def test_postgres_semantic_registry_round_trip(
    registry_connections: tuple[
        psycopg.AsyncConnection[object],
        psycopg.AsyncConnection[object],
    ],
) -> None:
    connection, _ = registry_connections
    registry = PostgresSemanticArtifactRegistry(connection)
    key = _key()
    ref = _ref()

    assert await registry.get(key) is None
    assert await registry.put(key, ref) == ref
    assert await registry.get(key) == ref
    assert await registry.put(key, ref) == ref


@pytest.mark.asyncio
async def test_postgres_semantic_registry_lock_serializes_same_identity(
    registry_connections: tuple[
        psycopg.AsyncConnection[object],
        psycopg.AsyncConnection[object],
    ],
) -> None:
    first_conn, second_conn = registry_connections
    first = PostgresSemanticArtifactRegistry(first_conn)
    second = PostgresSemanticArtifactRegistry(second_conn)
    key = _key()
    first_acquired = asyncio.Event()
    release_first = asyncio.Event()
    second_acquired = asyncio.Event()

    async def hold_first() -> None:
        async with first.lock(key):
            first_acquired.set()
            await release_first.wait()

    async def wait_second() -> None:
        await first_acquired.wait()
        async with second.lock(key):
            second_acquired.set()

    first_task = asyncio.create_task(hold_first())
    second_task = asyncio.create_task(wait_second())
    await first_acquired.wait()
    await asyncio.sleep(0.05)
    assert not second_acquired.is_set()

    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert second_acquired.is_set()
