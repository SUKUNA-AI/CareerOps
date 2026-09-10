from __future__ import annotations

from typing import Any

import pytest

from careerops_reranker import runtime as runtime_module
from careerops_reranker.config import RerankerRuntimeConfig
from careerops_reranker.runtime import (
    JinaRerankerRuntime,
    RerankerRuntimeIdentity,
    TokenBudgetExceeded,
)


class _FakeTokenizer:
    model_max_length = 32768

    def __call__(
        self,
        *,
        text: list[str],
        padding: bool,
        padding_side: str,
    ) -> dict[str, list[list[int]]]:
        del padding, padding_side
        return {"input_ids": [list(range(len(text[0].split())))]}


class _FakeModel:
    special_tokens = {
        "query_embed_token": "<query>",
        "doc_embed_token": "<doc>",
    }

    def _truncate_texts(
        self,
        query: str,
        documents: list[str],
        max_query_length: int,
        max_doc_length: int,
    ) -> tuple[str, list[str], list[int], int]:
        del max_query_length, max_doc_length
        return query, documents, [len(item.split()) for item in documents], len(query.split())

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int,
        return_embeddings: bool,
    ) -> list[dict[str, Any]]:
        del query, return_embeddings
        return [
            {
                "index": index,
                "relevance_score": 1.0 - index / 10,
                "document": document,
            }
            for index, document in enumerate(documents[:top_n])
        ]


def _formatter(
    query: str,
    docs: list[str],
    *,
    instruction: str | None,
    special_tokens: dict[str, str],
    no_thinking: bool,
) -> str:
    del instruction, special_tokens, no_thinking
    return " ".join(("query", query, "documents", *docs))


def _runtime() -> JinaRerankerRuntime:
    return JinaRerankerRuntime(
        identity=RerankerRuntimeIdentity(
            model_id="jinaai/jina-reranker-v3.5",
            model_revision="model-rev",
            model_code_revision="code-rev",
            tokenizer_revision="tokenizer-rev",
            runtime_backend="transformers-cuda",
            dtype_or_quantization="float16",
            torch_version="2.8.0",
            transformers_version="4.57.3",
        ),
        model=_FakeModel(),
        tokenizer=_FakeTokenizer(),
        prompt_formatter=_formatter,
    )


def test_runtime_identity_uses_installed_package_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = {"torch": "2.8.0+cu128", "transformers": "4.57.3"}
    monkeypatch.setattr(runtime_module, "_package_version", installed.__getitem__)
    config = RerankerRuntimeConfig.from_env({})

    identity = RerankerRuntimeIdentity.from_loaded_runtime(config)

    assert identity.torch_version == "2.8.0+cu128"
    assert identity.transformers_version == "4.57.3"


def test_runtime_counts_exact_formatted_prompt_tokens() -> None:
    runtime = _runtime()

    total = runtime.exact_total_tokens(
        "python requirement",
        ("python commercial experience", "sql project"),
    )

    assert total == 9


def test_runtime_enforces_budget_before_model_inference() -> None:
    runtime = _runtime()

    with pytest.raises(TokenBudgetExceeded, match="бюджете 8"):
        runtime.rerank(
            query="python requirement",
            documents=("python commercial experience", "sql project"),
            top_n=1,
            token_budget=8,
        )


def test_runtime_returns_plain_python_indices_and_scores() -> None:
    runtime = _runtime()

    results, total_tokens = runtime.rerank(
        query="python requirement",
        documents=("python commercial experience", "sql project"),
        top_n=2,
        token_budget=20,
    )

    assert total_tokens == 9
    assert results == (
        {"index": 0, "relevance_score": 1.0},
        {"index": 1, "relevance_score": 0.9},
    )