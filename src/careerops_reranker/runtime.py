"""Локальный transformers runtime для jina-reranker-v3.5"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .config import RerankerRuntimeConfig


class TokenBudgetExceeded(ValueError):
    """Точный listwise prompt превышает бюджет запроса"""


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError as exc:
        raise RuntimeError(f"не установлен runtime package: {distribution}") from exc


@dataclass(frozen=True, slots=True)
class RerankerRuntimeIdentity:
    model_id: str
    model_revision: str
    model_code_revision: str
    tokenizer_revision: str
    runtime_backend: str
    dtype_or_quantization: str
    torch_version: str
    transformers_version: str

    @classmethod
    def from_loaded_runtime(cls, config: RerankerRuntimeConfig) -> RerankerRuntimeIdentity:
        return cls(
            model_id=config.model_id,
            model_revision=config.model_revision,
            model_code_revision=config.model_code_revision,
            tokenizer_revision=config.tokenizer_revision,
            runtime_backend=config.runtime_backend,
            dtype_or_quantization=config.dtype_or_quantization,
            torch_version=_package_version("torch"),
            transformers_version=_package_version("transformers"),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "model_code_revision": self.model_code_revision,
            "tokenizer_revision": self.tokenizer_revision,
            "runtime_backend": self.runtime_backend,
            "dtype_or_quantization": self.dtype_or_quantization,
            "torch_version": self.torch_version,
            "transformers_version": self.transformers_version,
        }


class JinaRerankerRuntime:
    """Владеет одной GPU model и последовательно исполняет rerank requests"""

    def __init__(
        self,
        *,
        identity: RerankerRuntimeIdentity,
        model: Any,
        tokenizer: Any,
        prompt_formatter: Callable[..., str],
    ) -> None:
        self.identity = identity
        self._model = model
        self._tokenizer = tokenizer
        self._prompt_formatter = prompt_formatter
        self._inference_lock = threading.Lock()

    @classmethod
    def load(cls, config: RerankerRuntimeConfig) -> JinaRerankerRuntime:
        """Загружает exact pinned model/code/tokenizer revisions на заданное устройство"""

        if config.runtime_backend != "transformers-cuda":
            raise ValueError("первый P2-05 runtime поддерживает только transformers-cuda")
        if config.dtype_or_quantization != "float16":
            raise ValueError("первый P2-05 runtime поддерживает только float16 baseline")
        if not config.device.startswith("cuda"):
            raise ValueError("transformers-cuda runtime требует CUDA device")

        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "для careerops-reranker нужны optional dependencies reranker-runtime"
            ) from exc

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA недоступна для careerops-reranker")

        tokenizer = AutoTokenizer.from_pretrained(
            config.model_id,
            revision=config.tokenizer_revision,
            trust_remote_code=True,
        )
        model = AutoModel.from_pretrained(
            config.model_id,
            revision=config.model_revision,
            code_revision=config.model_code_revision,
            dtype=torch.float16,
            trust_remote_code=True,
        )
        model = model.to(config.device)
        model.eval()

        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.unk_token
            tokenizer.pad_token_id = tokenizer.convert_tokens_to_ids(tokenizer.pad_token)
        tokenizer.padding_side = "left"

        # Pinned tokenizer должен использоваться и внутри custom model.rerank
        model._tokenizer = tokenizer
        module = sys.modules.get(type(model).__module__)
        prompt_formatter = getattr(module, "format_docs_prompts_func", None)
        if not callable(prompt_formatter):
            raise RuntimeError("pinned Jina custom code не экспортирует prompt formatter")
        if not callable(getattr(model, "_truncate_texts", None)):
            raise RuntimeError("pinned Jina custom code не экспортирует truncation helper")
        if not callable(getattr(model, "rerank", None)):
            raise RuntimeError("pinned Jina model не экспортирует rerank")

        return cls(
            identity=RerankerRuntimeIdentity.from_loaded_runtime(config),
            model=model,
            tokenizer=tokenizer,
            prompt_formatter=prompt_formatter,
        )

    def _prompt_token_count(self, query: str, docs: list[str]) -> int:
        prompt = self._prompt_formatter(
            query,
            docs,
            instruction=None,
            special_tokens=self._model.special_tokens,
            no_thinking=True,
        )
        encoded = self._tokenizer(
            text=[prompt],
            padding=True,
            padding_side="left",
        )
        input_ids = encoded["input_ids"]
        return len(input_ids[0])

    def exact_total_tokens(self, query: str, documents: tuple[str, ...]) -> int:
        """Повторяет block splitting pinned model.rerank и считает exact prompt tokens"""

        query_value, docs, doc_lengths, query_length = self._model._truncate_texts(
            query,
            list(documents),
            1024,
            8192,
        )
        max_length = self._tokenizer.model_max_length
        length_capacity = max_length - 2 * query_length
        block_size = 125
        block_docs: list[str] = []
        total_tokens = 0

        for length, doc in zip(doc_lengths, docs, strict=True):
            block_docs.append(doc)
            length_capacity -= length
            if len(block_docs) >= block_size or length_capacity <= 8192:
                total_tokens += self._prompt_token_count(query_value, block_docs)
                block_docs = []
                length_capacity = max_length - 2 * query_length

        if block_docs:
            total_tokens += self._prompt_token_count(query_value, block_docs)
        return total_tokens

    def rerank(
        self,
        *,
        query: str,
        documents: tuple[str, ...],
        top_n: int,
        token_budget: int,
    ) -> tuple[tuple[dict[str, object], ...], int]:
        """Проверяет exact budget и выполняет один логический listwise rerank"""

        if not query.strip():
            raise ValueError("query не должен быть пустым")
        if not documents or any(not item.strip() for item in documents):
            raise ValueError("documents должны быть непустыми строками")
        if top_n <= 0 or top_n > len(documents):
            raise ValueError("top_n должен быть внутри candidate pool")
        if token_budget <= 0:
            raise ValueError("token_budget должен быть положительным")

        with self._inference_lock:
            total_tokens = self.exact_total_tokens(query, documents)
            if total_tokens > token_budget:
                raise TokenBudgetExceeded(
                    f"listwise prompt требует {total_tokens} tokens при бюджете {token_budget}"
                )
            raw_results = self._model.rerank(
                query,
                list(documents),
                top_n=top_n,
                return_embeddings=False,
            )

        results: list[dict[str, object]] = []
        for item in raw_results:
            results.append(
                {
                    "index": int(item["index"]),
                    "relevance_score": float(item["relevance_score"]),
                }
            )
        return tuple(results), total_tokens