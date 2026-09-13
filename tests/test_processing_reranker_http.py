from __future__ import annotations

import json

import httpx
import pytest

from careerops_processing.contracts import JinaVersionBundle
from careerops_processing.infrastructure.reranker_http import HttpJinaRerankerClient
from careerops_processing.selector import (
    RerankerProtocolError,
    RerankerTokenBudgetError,
    RerankerUnavailableError,
)


def _jina() -> JinaVersionBundle:
    return JinaVersionBundle(
        model_id="jinaai/jina-reranker-v3.5",
        model_revision="model-rev",
        model_code_revision="code-rev",
        tokenizer_revision="tokenizer-rev",
        runtime_backend="transformers-cuda",
        dtype_or_quantization="float16",
        torch_version="2.8.0",
        transformers_version="4.57.3",
        rendering_version="p205-render-v1",
        selection_version="p205-selection-v1",
        block_protocol="single-list-v1",
        token_budget=4096,
        top_k=2,
    )


def _response_payload(version: JinaVersionBundle) -> dict[str, object]:
    return {
        "runtime": {
            "model_id": version.model_id,
            "model_revision": version.model_revision,
            "model_code_revision": version.model_code_revision,
            "tokenizer_revision": version.tokenizer_revision,
            "runtime_backend": version.runtime_backend,
            "dtype_or_quantization": version.dtype_or_quantization,
            "torch_version": version.torch_version,
            "transformers_version": version.transformers_version,
        },
        "usage": {"total_tokens": 321},
        "results": [
            {"index": 1, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.3},
        ],
    }


@pytest.mark.asyncio
async def test_http_client_sends_expected_runtime_and_parses_response() -> None:
    version = _jina()
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_response_payload(version))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw_client:
        client = HttpJinaRerankerClient("http://reranker", client=raw_client)
        response = await client.rerank(
            query="query",
            documents=("a", "b"),
            top_n=2,
            token_budget=4096,
            expected_version=version,
        )

    assert captured["expected_runtime"] == _response_payload(version)["runtime"]
    assert captured["token_budget"] == 4096
    assert response.total_tokens == 321
    assert [(item.index, item.relevance_score) for item in response.results] == [
        (1, 0.9),
        (0, 0.3),
    ]


@pytest.mark.asyncio
async def test_http_client_rejects_runtime_identity_drift() -> None:
    version = _jina()
    payload = _response_payload(version)
    runtime = dict(payload["runtime"])  # type: ignore[arg-type]
    runtime["transformers_version"] = "4.58.0"
    payload["runtime"] = runtime

    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as raw_client:
        client = HttpJinaRerankerClient("http://reranker", client=raw_client)
        with pytest.raises(RerankerProtocolError, match="runtime identity"):
            await client.rerank(
                query="query",
                documents=("a",),
                top_n=1,
                token_budget=4096,
                expected_version=version,
            )


@pytest.mark.asyncio
async def test_http_client_maps_temporary_failure_to_unavailable() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(503))
    async with httpx.AsyncClient(transport=transport) as raw_client:
        client = HttpJinaRerankerClient("http://reranker", client=raw_client)
        with pytest.raises(RerankerUnavailableError):
            await client.rerank(
                query="query",
                documents=("a",),
                top_n=1,
                token_budget=4096,
                expected_version=_jina(),
            )


@pytest.mark.asyncio
async def test_http_client_maps_413_to_token_budget_error() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(413))
    async with httpx.AsyncClient(transport=transport) as raw_client:
        client = HttpJinaRerankerClient("http://reranker", client=raw_client)
        with pytest.raises(RerankerTokenBudgetError):
            await client.rerank(
                query="query",
                documents=("a",),
                top_n=1,
                token_budget=4096,
                expected_version=_jina(),
            )