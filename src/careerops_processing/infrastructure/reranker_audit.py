"""Durable S3 audit and PostgreSQL operational index for P2-05 reranker runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from psycopg import AsyncConnection
from psycopg import Error as PsycopgError

from careerops_processing.contracts import JinaVersionBundle
from careerops_processing.selector import (
    ObservedRerankerRuntime,
    RerankerProtocolError,
    RerankerRunObservation,
    RerankerRunStatus,
    RerankerUnavailableError,
)
from careerops_storage.s3 import S3JsonStore


@dataclass(frozen=True, slots=True)
class _JobContext:
    processing_job_id: UUID
    vacancy_id: int
    binding_id: int


def _expected_runtime_payload(version: JinaVersionBundle) -> dict[str, str]:
    return {
        "model_id": version.model_id,
        "model_revision": version.model_revision,
        "model_code_revision": version.model_code_revision,
        "tokenizer_revision": version.tokenizer_revision,
        "runtime_backend": version.runtime_backend,
        "dtype_or_quantization": version.dtype_or_quantization,
        "torch_version": version.torch_version,
        "transformers_version": version.transformers_version,
    }


def _runtime_fingerprint(runtime: ObservedRerankerRuntime | None) -> str | None:
    if runtime is None:
        return None
    body = json.dumps(
        runtime.as_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _missing_s3_key(exc: ClientError) -> bool:
    error = exc.response.get("Error", {})
    code = str(error.get("Code", ""))
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"404", "NoSuchKey", "NotFound"} or status == 404


class DurableRerankerAuditRecorder:
    """Persists one immutable operational audit bundle per actual reranker call."""

    def __init__(
        self,
        *,
        store: S3JsonStore,
        conn: AsyncConnection[Any],
    ) -> None:
        if not conn.autocommit:
            raise ValueError("DurableRerankerAuditRecorder требует autocommit connection")
        if store.settings.prefix.strip("/") != "reranker":
            raise ValueError("reranker audit store должен использовать prefix 'reranker'")
        self._store = store
        self._conn = conn
        self._job_cache: dict[str, _JobContext] = {}

    async def _resolve_job(self, input_fingerprint: str) -> _JobContext:
        cached = self._job_cache.get(input_fingerprint)
        if cached is not None:
            return cached

        try:
            cursor = await self._conn.execute(
                """
                SELECT id, vacancy_id, binding_id
                FROM careerops_v2.processing_jobs
                WHERE input_fingerprint = %s
                  AND status IN ('claimed', 'running')
                ORDER BY created_at DESC
                LIMIT 2
                """,
                (input_fingerprint,),
            )
            rows = await cursor.fetchall()
        except PsycopgError as exc:
            raise RerankerUnavailableError(
                "PostgreSQL reranker audit context unavailable"
            ) from exc

        if len(rows) != 1:
            raise RerankerProtocolError(
                "reranker audit не смог однозначно определить активную processing job"
            )
        context = _JobContext(
            processing_job_id=UUID(str(rows[0][0])),
            vacancy_id=int(rows[0][1]),
            binding_id=int(rows[0][2]),
        )
        self._job_cache[input_fingerprint] = context
        return context

    def _artifact_uri(self, run_id: UUID) -> str:
        return (
            f"s3://{self._store.settings.bucket}/"
            f"{self._store.settings.prefix.strip('/')}/run_id={run_id}/"
        )

    async def _put_once(self, key: str, payload: object) -> None:
        try:
            await self._store.head(key)
        except ClientError as exc:
            if not _missing_s3_key(exc):
                raise RerankerUnavailableError("reranker S3 audit HEAD failed") from exc
        except (BotoCoreError, OSError) as exc:
            raise RerankerUnavailableError("reranker S3 audit HEAD failed") from exc
        else:
            raise RerankerProtocolError("reranker audit object already exists")

        try:
            await self._store.put_json(key, payload)
        except (BotoCoreError, ClientError, OSError) as exc:
            raise RerankerUnavailableError("reranker S3 audit unavailable") from exc
        except (TypeError, ValueError) as exc:
            raise RerankerProtocolError("reranker S3 audit payload invalid") from exc

    async def _write_bundle(
        self,
        observation: RerankerRunObservation,
        context: _JobContext,
    ) -> str:
        run_prefix = f"run_id={observation.run_id}"
        request_payload = {
            "run_id": str(observation.run_id),
            "processing_job_id": str(context.processing_job_id),
            "vacancy_id": context.vacancy_id,
            "binding_id": context.binding_id,
            "requirement_id": observation.requirement_id,
            "query": observation.query,
            "ordered_pool_evidence_ids": list(observation.ordered_pool_evidence_ids),
            "pool_render_sha256": observation.pool_render_sha256,
            "top_k": observation.top_k,
            "token_budget": observation.token_budget,
            "expected_runtime": _expected_runtime_payload(observation.expected_version),
        }
        response_payload = {
            "run_id": str(observation.run_id),
            "actual_runtime": (
                None
                if observation.actual_runtime is None
                else observation.actual_runtime.as_dict()
            ),
            "results": [
                {
                    "evidence_id": item.evidence_id,
                    "rank": item.rank,
                    "relevance_score": item.relevance_score,
                }
                for item in observation.selected_candidates
            ],
            "total_tokens": observation.total_tokens,
        }
        pool_size = len(observation.ordered_pool_evidence_ids)
        selected_size = len(observation.selected_candidates)
        metrics_payload = {
            "run_id": str(observation.run_id),
            "started_at": observation.started_at.isoformat(),
            "finished_at": observation.finished_at.isoformat(),
            "latency_ms": observation.latency_ms,
            "pool_size": pool_size,
            "selected_size": selected_size,
            "status": observation.status.value,
            "error_class": observation.error_class,
            "token_budget_utilization": (
                None
                if observation.total_tokens is None
                else observation.total_tokens / observation.token_budget
            ),
            "selection_ratio": selected_size / pool_size,
        }

        await self._put_once(f"{run_prefix}/request.json", request_payload)
        await self._put_once(f"{run_prefix}/response.json", response_payload)
        await self._put_once(f"{run_prefix}/metrics.json", metrics_payload)
        return self._artifact_uri(observation.run_id)

    async def _write_index(
        self,
        observation: RerankerRunObservation,
        context: _JobContext,
        artifact_uri: str,
    ) -> None:
        runtime_fingerprint = _runtime_fingerprint(observation.actual_runtime)
        selected_size = len(observation.selected_candidates)
        try:
            await self._conn.execute(
                """
                INSERT INTO careerops_v2.reranker_runs (
                    id,
                    processing_job_id,
                    vacancy_id,
                    binding_id,
                    requirement_id,
                    status,
                    pool_size,
                    top_k,
                    selected_size,
                    total_tokens,
                    latency_ms,
                    runtime_fingerprint,
                    artifact_uri,
                    error_class,
                    started_at,
                    finished_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    observation.run_id,
                    context.processing_job_id,
                    context.vacancy_id,
                    context.binding_id,
                    observation.requirement_id,
                    observation.status.value,
                    len(observation.ordered_pool_evidence_ids),
                    observation.top_k,
                    selected_size,
                    observation.total_tokens,
                    observation.latency_ms,
                    runtime_fingerprint,
                    artifact_uri,
                    observation.error_class,
                    observation.started_at,
                    observation.finished_at,
                ),
            )
        except PsycopgError as exc:
            raise RerankerUnavailableError("PostgreSQL reranker audit index unavailable") from exc

    async def record(self, observation: RerankerRunObservation) -> None:
        if observation.status is RerankerRunStatus.SUCCESS and observation.error_class is not None:
            raise RerankerProtocolError("successful reranker audit cannot contain error_class")
        if observation.status is RerankerRunStatus.ERROR and not observation.error_class:
            raise RerankerProtocolError("failed reranker audit requires error_class")
        if observation.finished_at < observation.started_at:
            raise RerankerProtocolError("reranker audit timestamps are reversed")
        if observation.latency_ms < 0:
            raise RerankerProtocolError("reranker audit latency must be non-negative")
        if not observation.ordered_pool_evidence_ids:
            raise RerankerProtocolError("reranker audit pool must not be empty")
        if observation.top_k <= 0 or observation.top_k > len(observation.ordered_pool_evidence_ids):
            raise RerankerProtocolError("reranker audit top_k is outside candidate pool")
        if observation.total_tokens is not None and observation.total_tokens < 0:
            raise RerankerProtocolError("reranker audit total_tokens must be non-negative")

        context = await self._resolve_job(observation.input_fingerprint)
        artifact_uri = await self._write_bundle(observation, context)
        await self._write_index(observation, context, artifact_uri)
