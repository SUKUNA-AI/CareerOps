"""Настройки отдельного GPU runtime Jina reranker"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_JINA_MODEL_ID = "jinaai/jina-reranker-v3.5"
DEFAULT_JINA_REVISION = "e8a93f33f0b22108f8c2364f8484ce3422552fbc"


class RerankerRuntimeConfig(BaseModel):
    """Pinned runtime identity и сетевые настройки reranker service"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    model_code_revision: str = Field(min_length=1)
    tokenizer_revision: str = Field(min_length=1)
    runtime_backend: str = Field(min_length=1)
    dtype_or_quantization: str = Field(min_length=1)
    device: str = Field(min_length=1)
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)

    @field_validator(
        "model_id",
        "model_revision",
        "model_code_revision",
        "tokenizer_revision",
        "runtime_backend",
        "dtype_or_quantization",
        "device",
        "host",
    )
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reranker runtime setting не должен быть пустым")
        return stripped

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> RerankerRuntimeConfig:
        env = os.environ if environ is None else environ

        def integer(name: str, default: str) -> int:
            raw = env.get(name, default).strip()
            try:
                return int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} должен быть integer") from exc

        model_revision = env.get(
            "CAREEROPS_RERANKER_MODEL_REVISION",
            DEFAULT_JINA_REVISION,
        ).strip()
        return cls(
            model_id=env.get("CAREEROPS_RERANKER_MODEL_ID", DEFAULT_JINA_MODEL_ID),
            model_revision=model_revision,
            model_code_revision=env.get(
                "CAREEROPS_RERANKER_MODEL_CODE_REVISION",
                model_revision,
            ),
            tokenizer_revision=env.get(
                "CAREEROPS_RERANKER_TOKENIZER_REVISION",
                model_revision,
            ),
            runtime_backend=env.get(
                "CAREEROPS_RERANKER_RUNTIME_BACKEND",
                "transformers-cuda",
            ),
            dtype_or_quantization=env.get(
                "CAREEROPS_RERANKER_DTYPE",
                "float16",
            ),
            device=env.get("CAREEROPS_RERANKER_DEVICE", "cuda"),
            host=env.get("CAREEROPS_RERANKER_HOST", "0.0.0.0"),
            port=integer("CAREEROPS_RERANKER_PORT", "18082"),
        )
