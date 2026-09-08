"""Жизненный цикл worker для постоянной очереди Processing v2"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from .queue import ProcessingJobLeaseLost, ProcessingJobRecord, ProcessingJobStore


class ProcessingExecutionDisposition(StrEnum):
    """Результат executor перед изменением состояния постоянной очереди"""

    SUCCEEDED = "succeeded"
    DEFERRED = "deferred"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"


@dataclass(frozen=True, slots=True)
class ProcessingExecutionResult:
    """Типизированный результат executor перед записью состояния очереди"""

    disposition: ProcessingExecutionDisposition
    result_artifact_uri: str | None = None
    error_category: str | None = None
    next_attempt_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.disposition is ProcessingExecutionDisposition.SUCCEEDED:
            if self.result_artifact_uri is None or not self.result_artifact_uri.startswith("s3://"):
                raise ValueError("successful execution requires an s3:// result artifact URI")
            if self.error_category is not None or self.next_attempt_at is not None:
                raise ValueError("successful execution cannot carry retry/failure metadata")
            return

        if self.result_artifact_uri is not None:
            raise ValueError("non-successful execution cannot publish a result artifact")
        if self.error_category is None or not self.error_category.strip():
            raise ValueError("non-successful execution requires an error_category")
        if self.error_category != self.error_category.strip():
            raise ValueError("error_category must not contain surrounding whitespace")

        if self.disposition in {
            ProcessingExecutionDisposition.DEFERRED,
            ProcessingExecutionDisposition.RETRYABLE_FAILURE,
        }:
            if self.next_attempt_at is None:
                raise ValueError("retryable execution requires next_attempt_at")
            if self.next_attempt_at.tzinfo is None or self.next_attempt_at.utcoffset() is None:
                raise ValueError("next_attempt_at must be timezone-aware")
            return

        if self.next_attempt_at is not None:
            raise ValueError("terminal failure cannot carry next_attempt_at")


class ProcessingExecutor(Protocol):
    """Исполнитель бизнес-стадий, подключённый к жизненному циклу worker"""

    async def execute(self, job: ProcessingJobRecord) -> ProcessingExecutionResult: ...


@dataclass(frozen=True, slots=True)
class ProcessingWorkerPolicy:
    lease_seconds: int = 300
    unexpected_failure_delay: timedelta = timedelta(minutes=5)
    idle_sleep_seconds: float = 1.0

    def __post_init__(self) -> None:
        if isinstance(self.lease_seconds, bool) or self.lease_seconds < 3:
            raise ValueError("lease_seconds must be >= 3")
        if self.unexpected_failure_delay <= timedelta(0):
            raise ValueError("unexpected_failure_delay must be positive")
        if self.idle_sleep_seconds <= 0:
            raise ValueError("idle_sleep_seconds must be positive")


class ProcessingWorker:
    """Забирает задачи, продлевает lease, исполняет их и фиксирует результат"""

    def __init__(
        self,
        *,
        store: ProcessingJobStore,
        executor: ProcessingExecutor,
        worker_id: str,
        policy: ProcessingWorkerPolicy | None = None,
    ) -> None:
        normalized_worker = worker_id.strip()
        if not normalized_worker:
            raise ValueError("worker_id must not be empty")
        self._store = store
        self._executor = executor
        self._worker_id = normalized_worker
        self._policy = policy or ProcessingWorkerPolicy()

    async def run_one(self) -> bool:
        """Обрабатывает не больше одной задачи и возвращает False, если работы нет"""

        job = await self._store.claim_next(
            worker_id=self._worker_id,
            lease_seconds=self._policy.lease_seconds,
        )
        if job is None:
            return False

        await self._store.mark_running(job)
        stop_heartbeat = asyncio.Event()
        executor_task = asyncio.create_task(
            self._executor.execute(job),
            name=f"processing-executor-{job.id}",
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat(job, stop_heartbeat),
            name=f"processing-heartbeat-{job.id}",
        )
        wait_set: set[asyncio.Task[Any]] = {executor_task, heartbeat_task}

        try:
            done, _ = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)

            if heartbeat_task in done:
                if heartbeat_task.cancelled():
                    heartbeat_error: BaseException = ProcessingJobLeaseLost(
                        f"processing heartbeat cancelled for {job.id}"
                    )
                else:
                    heartbeat_error = heartbeat_task.exception() or RuntimeError(
                        "processing heartbeat stopped unexpectedly"
                    )
                executor_task.cancel()
                await asyncio.gather(executor_task, return_exceptions=True)
                raise heartbeat_error

            stop_heartbeat.set()
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

            try:
                result = executor_task.result()
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._store.retryable_failure(
                    job,
                    error_category="processing.unhandled_exception",
                    next_attempt_at=datetime.now(UTC) + self._policy.unexpected_failure_delay,
                )
                return True

            await self._commit_result(job, result)
            return True
        finally:
            stop_heartbeat.set()
            for task in (executor_task, heartbeat_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(executor_task, heartbeat_task, return_exceptions=True)

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Обрабатывает очередь до остановки и переживает ожидаемую потерю lease"""

        while not stop.is_set():
            try:
                processed = await self.run_one()
            except ProcessingJobLeaseLost:
                continue
            if processed:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._policy.idle_sleep_seconds)
            except TimeoutError:
                pass

    async def _heartbeat(
        self,
        job: ProcessingJobRecord,
        stop: asyncio.Event,
    ) -> None:
        interval = self._policy.lease_seconds / 3
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                await self._store.renew_lease(
                    job,
                    lease_seconds=self._policy.lease_seconds,
                )

    async def _commit_result(
        self,
        job: ProcessingJobRecord,
        result: ProcessingExecutionResult,
    ) -> None:
        if result.disposition is ProcessingExecutionDisposition.SUCCEEDED:
            assert result.result_artifact_uri is not None
            await self._store.succeed(job, result_artifact_uri=result.result_artifact_uri)
            return

        assert result.error_category is not None
        if result.disposition is ProcessingExecutionDisposition.DEFERRED:
            assert result.next_attempt_at is not None
            await self._store.defer(
                job,
                error_category=result.error_category,
                next_attempt_at=result.next_attempt_at,
            )
            return

        if result.disposition is ProcessingExecutionDisposition.RETRYABLE_FAILURE:
            assert result.next_attempt_at is not None
            await self._store.retryable_failure(
                job,
                error_category=result.error_category,
                next_attempt_at=result.next_attempt_at,
            )
            return

        await self._store.terminal_failure(job, error_category=result.error_category)
