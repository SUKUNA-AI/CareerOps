from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from careerops_processing.queue import (
    ProcessingJobLeaseLost,
    ProcessingJobRecord,
    ProcessingJobStatus,
    ProcessingPairKey,
    ProcessingWorkSpec,
)
from careerops_processing.worker import (
    ProcessingExecutionDisposition,
    ProcessingExecutionResult,
    ProcessingWorker,
    ProcessingWorkerPolicy,
)


class _FakeStore:
    def __init__(self, jobs: list[ProcessingJobRecord]) -> None:
        self.jobs = list(jobs)
        self.transitions: list[tuple[str, object]] = []
        self.renewed = asyncio.Event()
        self.fail_renewal = False

    async def reconcile_current(self, spec: ProcessingWorkSpec) -> UUID:
        del spec
        return uuid4()

    async def withdraw_pair(self, pair: ProcessingPairKey, *, reason: str) -> int:
        self.transitions.append(("withdraw", (pair, reason)))
        return 0

    async def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> ProcessingJobRecord | None:
        self.transitions.append(("claim", (worker_id, lease_seconds)))
        return self.jobs.pop(0) if self.jobs else None

    async def mark_running(self, job: ProcessingJobRecord) -> None:
        self.transitions.append(("running", job.id))

    async def renew_lease(
        self,
        job: ProcessingJobRecord,
        *,
        lease_seconds: int = 300,
    ) -> None:
        self.transitions.append(("renew", (job.id, lease_seconds)))
        self.renewed.set()
        if self.fail_renewal:
            raise ProcessingJobLeaseLost("lease lost")

    async def succeed(self, job: ProcessingJobRecord, *, result_artifact_uri: str) -> None:
        self.transitions.append(("succeeded", (job.id, result_artifact_uri)))

    async def defer(
        self,
        job: ProcessingJobRecord,
        *,
        error_category: str,
        next_attempt_at: datetime,
    ) -> None:
        self.transitions.append(("deferred", (job.id, error_category, next_attempt_at)))

    async def retryable_failure(
        self,
        job: ProcessingJobRecord,
        *,
        error_category: str,
        next_attempt_at: datetime,
    ) -> None:
        self.transitions.append(("retryable", (job.id, error_category, next_attempt_at)))

    async def terminal_failure(
        self,
        job: ProcessingJobRecord,
        *,
        error_category: str,
    ) -> None:
        self.transitions.append(("terminal", (job.id, error_category)))

    async def cancel(self, job: ProcessingJobRecord, *, reason: str = "cancelled") -> None:
        self.transitions.append(("cancelled", (job.id, reason)))


class _ResultExecutor:
    def __init__(self, result: ProcessingExecutionResult) -> None:
        self.result = result

    async def execute(self, job: ProcessingJobRecord) -> ProcessingExecutionResult:
        del job
        return self.result


class _FailingExecutor:
    async def execute(self, job: ProcessingJobRecord) -> ProcessingExecutionResult:
        del job
        raise RuntimeError("boom")


class _SlowExecutor:
    def __init__(self) -> None:
        self.cancelled = asyncio.Event()

    async def execute(self, job: ProcessingJobRecord) -> ProcessingExecutionResult:
        del job
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("unreachable")


def _job() -> ProcessingJobRecord:
    now = datetime.now(UTC)
    return ProcessingJobRecord(
        id=uuid4(),
        vacancy_id=1,
        binding_id=2,
        binding_version=1,
        input_fingerprint="a" * 64,
        input_manifest_uri="s3://careerops-artifacts/manifest.json",
        pipeline_version="processing-v2-test",
        policy_version="policy-v1",
        status=ProcessingJobStatus.CLAIMED,
        attempt_count=1,
        lease_owner="worker-a",
        lease_token=uuid4(),
        leased_at=now,
        lease_expires_at=now + timedelta(minutes=5),
    )


def test_execution_result_validates_disposition_contract() -> None:
    with pytest.raises(ValueError, match="s3://"):
        ProcessingExecutionResult(disposition=ProcessingExecutionDisposition.SUCCEEDED)

    with pytest.raises(ValueError, match="next_attempt_at"):
        ProcessingExecutionResult(
            disposition=ProcessingExecutionDisposition.RETRYABLE_FAILURE,
            error_category="reranker.timeout",
        )

    with pytest.raises(ValueError, match="surrounding whitespace"):
        ProcessingExecutionResult(
            disposition=ProcessingExecutionDisposition.TERMINAL_FAILURE,
            error_category=" bad.category ",
        )


@pytest.mark.asyncio
async def test_worker_commits_success_only_after_executor_artifact() -> None:
    job = _job()
    store = _FakeStore([job])
    artifact_uri = "s3://careerops-artifacts/results/result.json"
    worker = ProcessingWorker(
        store=store,
        executor=_ResultExecutor(
            ProcessingExecutionResult(
                disposition=ProcessingExecutionDisposition.SUCCEEDED,
                result_artifact_uri=artifact_uri,
            )
        ),
        worker_id="worker-a",
    )

    assert await worker.run_one() is True
    assert ("running", job.id) in store.transitions
    assert ("succeeded", (job.id, artifact_uri)) in store.transitions


@pytest.mark.asyncio
async def test_worker_routes_deferred_result_without_losing_work() -> None:
    job = _job()
    store = _FakeStore([job])
    next_attempt = datetime.now(UTC) + timedelta(minutes=10)
    worker = ProcessingWorker(
        store=store,
        executor=_ResultExecutor(
            ProcessingExecutionResult(
                disposition=ProcessingExecutionDisposition.DEFERRED,
                error_category="reranker.unavailable",
                next_attempt_at=next_attempt,
            )
        ),
        worker_id="worker-a",
    )

    assert await worker.run_one() is True
    assert ("deferred", (job.id, "reranker.unavailable", next_attempt)) in store.transitions


@pytest.mark.asyncio
async def test_unhandled_executor_exception_becomes_retryable_failure() -> None:
    job = _job()
    store = _FakeStore([job])
    worker = ProcessingWorker(
        store=store,
        executor=_FailingExecutor(),
        worker_id="worker-a",
        policy=ProcessingWorkerPolicy(unexpected_failure_delay=timedelta(seconds=30)),
    )

    before = datetime.now(UTC)
    assert await worker.run_one() is True
    after = datetime.now(UTC)

    retry_transitions = [item for item in store.transitions if item[0] == "retryable"]
    assert len(retry_transitions) == 1
    payload = retry_transitions[0][1]
    assert isinstance(payload, tuple)
    assert payload[0] == job.id
    assert payload[1] == "processing.unhandled_exception"
    next_attempt = payload[2]
    assert isinstance(next_attempt, datetime)
    assert before + timedelta(seconds=30) <= next_attempt <= after + timedelta(seconds=30)


@pytest.mark.asyncio
async def test_heartbeat_lease_loss_cancels_inflight_executor() -> None:
    job = _job()
    store = _FakeStore([job])
    store.fail_renewal = True
    executor = _SlowExecutor()
    worker = ProcessingWorker(
        store=store,
        executor=executor,
        worker_id="worker-a",
        policy=ProcessingWorkerPolicy(lease_seconds=3),
    )

    with pytest.raises(ProcessingJobLeaseLost):
        await worker.run_one()

    assert store.renewed.is_set()
    assert executor.cancelled.is_set()


@pytest.mark.asyncio
async def test_worker_returns_false_when_queue_is_empty() -> None:
    store = _FakeStore([])
    worker = ProcessingWorker(
        store=store,
        executor=_FailingExecutor(),
        worker_id="worker-a",
    )

    assert await worker.run_one() is False
