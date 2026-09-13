"""HTTP boundary между Processing и отдельным Jina reranker runtime"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from careerops_processing.contracts import JinaVersionBundle
from careerops_processing.selector import (
    RerankerProtocolError,
    RerankerResponse,
    RerankerScore,
    RerankerTokenBudgetError,
    RerankerUnavailableError,
)


class _RuntimeIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    model_code_revision: str = Field(min_length=1)
    tokenizer_revision: str = Field(min_length=1)
    runtime_backend: str = Field(min_length=1)
    dtype_or_quantization: str = Field(min_length=1)
    torch_version: str = Field(min_length=1)
    transformers_version: str = Field(min_length=1)


class _Usage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    total_tokens: int = Field(ge=0)


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    index: int = Field(ge=0)
    relevance_score: float = Field(allow_inf_nan=False)


class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    runtime: _RuntimeIdentity
    usage: _Usage
    results: tuple[_Result, ...]


class HttpJinaRerankerClient:
    """Вызывает внутренний `/v1/rerank` и проверяет точную runtime identity"""

    def __init__(
        self,
        endpoint_url: str,
        *,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        endpoint = endpoint_url.strip().rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("reranker endpoint должен начинаться с http:// или https://")
        if timeout_seconds <= 0:
            raise ValueError("reranker timeout должен быть положительным")
        self._endpoint_url = endpoint
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = False

    async def __aenter__(self) -> HttpJinaRerankerClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
            self._owns_client = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
            self._owns_client = False

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HttpJinaRerankerClient требует async context manager")
        return self._client

    @staticmethod
    def _runtime_payload(version: JinaVersionBundle) -> dict[str, str]:
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

    @classmethod
    def _runtime_matches(
        cls,
        runtime: _RuntimeIdentity,
        expected: JinaVersionBundle,
    ) -> bool:
        return runtime.model_dump(mode="json") == cls._runtime_payload(expected)

    async def rerank(
        self,
        *,
        query: str,
        documents: tuple[str, ...],
        top_n: int,
        token_budget: int,
        expected_version: JinaVersionBundle,
    ) -> RerankerResponse:
        if top_n <= 0 or top_n > len(documents):
            raise RerankerProtocolError("top_n должен быть внутри candidate pool")
        if token_budget <= 0:
            raise RerankerProtocolError("token_budget должен быть положительным")

        payload: dict[str, Any] = {
            "query": query,
            "documents": list(documents),
            "top_n": top_n,
            "token_budget": token_budget,
            "expected_runtime": self._runtime_payload(expected_version),
        }
        client = self._require_client()
        try:
            response = await client.post(f"{self._endpoint_url}/v1/rerank", json=payload)
        except httpx.HTTPError as exc:
            raise RerankerUnavailableError("reranker HTTP request failed") from exc

        if response.status_code in {429, 502, 503, 504}:
            raise RerankerUnavailableError(
                f"reranker временно недоступен: HTTP {response.status_code}"
            )
        if response.status_code == 413:
            raise RerankerTokenBudgetError("reranker отклонил запрос по token budget")
        if response.status_code < 200 or response.status_code >= 300:
            raise RerankerProtocolError(
                f"reranker отклонил запрос: HTTP {response.status_code}"
            )

        try:
            parsed = _Response.model_validate_json(response.content)
        except ValueError as exc:
            raise RerankerProtocolError("reranker вернул неверный JSON contract") from exc
        if not self._runtime_matches(parsed.runtime, expected_version):
            raise RerankerProtocolError("reranker runtime identity не совпадает с manifest")

        return RerankerResponse(
            results=tuple(
                RerankerScore(index=item.index, relevance_score=item.relevance_score)
                for item in parsed.results
            ),
            total_tokens=parsed.usage.total_tokens,
        )