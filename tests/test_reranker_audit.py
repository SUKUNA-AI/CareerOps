from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from botocore.exceptions import ClientError

from careerops_processing.contracts import EvidenceCandidate, JinaVersionBundle
from careerops_processing.infrastructure.reranker_audit import DurableRerankerAuditRecorder
from careerops_processing.selector import (
    ObservedRerankerRuntime,
    RerankerProtocolError,
    RerankerRunObservation,
    RerankerRunStatus,
)


class _Cursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


class _FakeConn:
    autocommit = True

    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        self.inserts: list[tuple[object, ...]] = []

    async def execute(self, query: str, params: tuple[object, ...]) -> _Cursor:
        if "SELECT id, vacancy_id, binding_id" in query:
            return _Cursor([(self.job_id, 11, 22)])
        self.inserts.append(params)
        return _Cursor([])


class _FakeStore:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(bucket="careerops-artifacts", prefix="reranker")
        self.objects: dict[str, object] = {}

    async def head(self, key: str) -> dict[str, object]:
        if key in self.objects:
            return {"Key": key}
        raise ClientError(
            {
                "Error": {"Code": "NoSuchKey", "Message": "missing"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "HeadObject",
        )

    async def put_json(self, key: str, payload: object) -> None:
        self.objects[key] = payload


def _jina() -> JinaVersionBundle:
    return JinaVersionBundle(
        model_id="jinaai/jina-reranker-v3.5",
        model_revision="model-rev",
        model_code_revision="code-rev",
        tokenizer_revision="tokenizer-rev",
        runtime_backend="transformers-cuda",
        dtype_or_quantization="float16",
        torch_version="2.14.0+cu132",
        transformers_version="4.57.3",
        rendering_version="p205-render-v1",
        selection_version="p205-selection-v1",
        block_protocol="single-list-v1",
        token_budget=4096,
        top_k=2,
    )


def _runtime() -> ObservedRerankerRuntime:
    return ObservedRerankerRuntime(
        model_id="jinaai/jina-reranker-v3.5",
        model_revision="model-rev",
        model_code_revision="code-rev",
        tokenizer_revision="tokenizer-rev",
        runtime_backend="transformers-cuda",
        dtype_or_quantization="float16",
        torch_version="2.14.0+cu132",
        transformers_version="4.57.3",
    )


def _observation(run_id: UUID) -> RerankerRunObservation:
    started = datetime.now(UTC)
    return RerankerRunObservation(
        run_id=run_id,
        input_fingerprint="a" * 64,
        requirement_id="req-python",
        query="Python commercial experience",
        ordered_pool_evidence_ids=("ev-a", "ev-b", "ev-c"),
        pool_render_sha256="b" * 64,
        top_k=2,
        token_budget=4096,
        expected_version=_jina(),
        actual_runtime=_runtime(),
        selected_candidates=(
            EvidenceCandidate(evidence_id="ev-b", rank=1, relevance_score=0.9),
            EvidenceCandidate(evidence_id="ev-a", rank=2, relevance_score=0.4),
        ),
        total_tokens=842,
        started_at=started,
        finished_at=started + timedelta(milliseconds=123),
        latency_ms=123,
        status=RerankerRunStatus.SUCCESS,
    )


@pytest.mark.asyncio
async def test_audit_recorder_writes_three_objects_and_operational_index() -> None:
    run_id = uuid4()
    job_id = uuid4()
    store = _FakeStore()
    conn = _FakeConn(job_id)
    recorder = DurableRerankerAuditRecorder(store=store, conn=conn)  # type: ignore[arg-type]

    await recorder.record(_observation(run_id))

    prefix = f"run_id={run_id}"
    assert set(store.objects) == {
        f"{prefix}/request.json",
        f"{prefix}/response.json",
        f"{prefix}/metrics.json",
    }
    request_payload = store.objects[f"{prefix}/request.json"]
    assert isinstance(request_payload, dict)
    assert request_payload["processing_job_id"] == str(job_id)
    assert request_payload["vacancy_id"] == 11
    assert request_payload["binding_id"] == 22
    assert "documents" not in request_payload

    response_payload = store.objects[f"{prefix}/response.json"]
    assert isinstance(response_payload, dict)
    assert response_payload["results"][0]["evidence_id"] == "ev-b"  # type: ignore[index]

    metrics_payload = store.objects[f"{prefix}/metrics.json"]
    assert isinstance(metrics_payload, dict)
    assert metrics_payload["token_budget_utilization"] == pytest.approx(842 / 4096)
    assert metrics_payload["selection_ratio"] == pytest.approx(2 / 3)

    assert len(conn.inserts) == 1
    insert = conn.inserts[0]
    assert insert[0] == run_id
    assert insert[1] == job_id
    assert insert[2:4] == (11, 22)
    assert insert[12] == f"s3://careerops-artifacts/reranker/run_id={run_id}/"


@pytest.mark.asyncio
async def test_audit_recorder_refuses_to_overwrite_existing_run_bundle() -> None:
    run_id = uuid4()
    store = _FakeStore()
    recorder = DurableRerankerAuditRecorder(
        store=store,  # type: ignore[arg-type]
        conn=_FakeConn(uuid4()),  # type: ignore[arg-type]
    )
    await recorder.record(_observation(run_id))

    with pytest.raises(RerankerProtocolError, match="already exists"):
        await recorder.record(_observation(run_id))
