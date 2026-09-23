"""GPU-backed P2-05 calibration runner over the production evidence selector."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from careerops_processing.contracts import JinaVersionBundle, RequirementSet, ResumeEvidenceSet
from careerops_processing.infrastructure.reranker_http import HttpJinaRerankerClient
from careerops_processing.selector import EvidenceCandidateSelector, RerankerClient

from .models import P205Prediction

_RENDERING_VERSION = "p205-render-v1"
_SELECTION_VERSION = "p205-selection-v1"
_BLOCK_PROTOCOL = "single-list-v1"


class P205RequirementAlignment(BaseModel):
    """Explicit gold requirement -> runtime requirement alignment from P2-04."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    gold_requirement_index: int = Field(ge=0)
    requirement_id: str = Field(min_length=1)


class P205RuntimeCase(BaseModel):
    """One vacancy x resume pair ready for real P2-05 listwise reranking."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    pair_id: str = Field(min_length=1)
    requirement_set: RequirementSet
    resume_evidence_set: ResumeEvidenceSet
    alignments: tuple[P205RequirementAlignment, ...] = ()

    @model_validator(mode="after")
    def validate_alignments(self) -> P205RuntimeCase:
        gold_indices = [item.gold_requirement_index for item in self.alignments]
        if len(gold_indices) != len(set(gold_indices)):
            raise ValueError("gold_requirement_index must be unique within a P2-05 runtime case")

        known_requirement_ids = {
            item.requirement_id for item in self.requirement_set.requirements
        }
        unknown = sorted(
            {
                item.requirement_id
                for item in self.alignments
                if item.requirement_id not in known_requirement_ids
            }
        )
        if unknown:
            raise ValueError(
                "P2-05 alignment references unknown requirement ids: " + ", ".join(unknown)
            )
        return self


class _ReadyRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    model_code_revision: str = Field(min_length=1)
    tokenizer_revision: str = Field(min_length=1)
    runtime_backend: str = Field(min_length=1)
    dtype_or_quantization: str = Field(min_length=1)
    torch_version: str = Field(min_length=1)
    transformers_version: str = Field(min_length=1)


class _ReadyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["ready"]
    runtime: _ReadyRuntime


def _canonical_sha256(model: BaseModel) -> str:
    body = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _input_fingerprint(
    case: P205RuntimeCase,
    *,
    requirement_set_sha256: str,
    resume_evidence_set_sha256: str,
    jina: JinaVersionBundle,
) -> str:
    body = json.dumps(
        {
            "pair_id": case.pair_id,
            "requirement_set_sha256": requirement_set_sha256,
            "resume_evidence_set_sha256": resume_evidence_set_sha256,
            "jina": jina.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def load_p205_runtime_cases(path: Path) -> tuple[P205RuntimeCase, ...]:
    cases: list[P205RuntimeCase] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            cases.append(P205RuntimeCase.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"invalid P2-05 runtime case at {path}:{line_number}") from exc

    if not cases:
        raise ValueError("P2-05 runtime cases file is empty")
    pair_ids = [item.pair_id for item in cases]
    if len(pair_ids) != len(set(pair_ids)):
        raise ValueError("P2-05 runtime case pair_id values must be unique")
    return tuple(cases)


async def discover_jina_version(
    endpoint_url: str,
    *,
    top_k: int,
    token_budget: int,
    timeout_seconds: float,
) -> JinaVersionBundle:
    endpoint = endpoint_url.strip().rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        raise ValueError("reranker endpoint must start with http:// or https://")
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if token_budget <= 0:
        raise ValueError("token_budget must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(f"{endpoint}/readyz")
        response.raise_for_status()
        ready = _ReadyResponse.model_validate_json(response.content)

    return JinaVersionBundle(
        **ready.runtime.model_dump(mode="python"),
        rendering_version=_RENDERING_VERSION,
        selection_version=_SELECTION_VERSION,
        block_protocol=_BLOCK_PROTOCOL,
        token_budget=token_budget,
        top_k=top_k,
    )


async def run_p205_cases(
    cases: tuple[P205RuntimeCase, ...],
    *,
    client: RerankerClient,
    jina: JinaVersionBundle,
) -> tuple[P205Prediction, ...]:
    """Run full evidence pools through the production P2-05 selector."""

    selector = EvidenceCandidateSelector(client)
    predictions: list[P205Prediction] = []

    for case in cases:
        requirement_set_sha256 = _canonical_sha256(case.requirement_set)
        resume_evidence_set_sha256 = _canonical_sha256(case.resume_evidence_set)
        candidate_set = await selector.select(
            input_fingerprint=_input_fingerprint(
                case,
                requirement_set_sha256=requirement_set_sha256,
                resume_evidence_set_sha256=resume_evidence_set_sha256,
                jina=jina,
            ),
            requirement_set_ref_sha256=requirement_set_sha256,
            resume_evidence_set_ref_sha256=resume_evidence_set_sha256,
            requirement_set=case.requirement_set,
            evidence_set=case.resume_evidence_set,
            jina=jina,
        )
        selection_by_requirement = {
            item.requirement_id: item for item in candidate_set.selections
        }
        for alignment in case.alignments:
            selection = selection_by_requirement[alignment.requirement_id]
            predictions.append(
                P205Prediction(
                    pair_id=case.pair_id,
                    gold_requirement_index=alignment.gold_requirement_index,
                    ranked_evidence_refs=tuple(
                        item.evidence_id for item in selection.candidates
                    ),
                )
            )

    return tuple(
        sorted(
            predictions,
            key=lambda item: (item.pair_id, item.gold_requirement_index),
        )
    )


def write_p205_predictions(path: Path, predictions: tuple[P205Prediction, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        "".join(f"{item.model_dump_json()}\n" for item in predictions),
        encoding="utf-8",
    )
    temporary.replace(path)


async def execute_p205_calibration(
    *,
    cases_path: Path,
    endpoint: str,
    output_path: Path,
    top_k: int,
    token_budget: int,
    timeout_seconds: float,
) -> dict[str, object]:
    """Execute the live full-pool P2-05 calibration against one Jina endpoint."""

    cases = load_p205_runtime_cases(cases_path)
    jina = await discover_jina_version(
        endpoint,
        top_k=top_k,
        token_budget=token_budget,
        timeout_seconds=timeout_seconds,
    )
    async with HttpJinaRerankerClient(
        endpoint,
        timeout_seconds=timeout_seconds,
    ) as client:
        predictions = await run_p205_cases(cases, client=client, jina=jina)
    write_p205_predictions(output_path, predictions)
    return {
        "runtime_cases": len(cases),
        "prediction_rows": len(predictions),
        "top_k": jina.top_k,
        "token_budget": jina.token_budget,
        "runtime": {
            "model_id": jina.model_id,
            "model_revision": jina.model_revision,
            "model_code_revision": jina.model_code_revision,
            "tokenizer_revision": jina.tokenizer_revision,
            "runtime_backend": jina.runtime_backend,
            "dtype_or_quantization": jina.dtype_or_quantization,
            "torch_version": jina.torch_version,
            "transformers_version": jina.transformers_version,
        },
        "output": str(output_path),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run full-pool P2-05 Jina calibration and emit p205.jsonl"
    )
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--token-budget", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    summary = asyncio.run(
        execute_p205_calibration(
            cases_path=args.cases,
            endpoint=args.endpoint,
            output_path=args.output,
            top_k=args.top_k,
            token_budget=args.token_budget,
            timeout_seconds=args.timeout_seconds,
        )
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
