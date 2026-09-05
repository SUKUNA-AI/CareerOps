from __future__ import annotations

import json
from contextlib import AbstractAsyncContextManager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import UUID

from careerops_storage import S3ObjectRef

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
APPLICATION_RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
VACANCY_ID = "136655995"
RUN_PREFIX = f"batches/date=2026-08-30/run_id={RUN_ID}"
CANDIDATE_PREFIX = f"{RUN_PREFIX}/candidates/vacancy_id={VACANCY_ID}"
APPLICATION_PREFIX = (
    "applications/date=2026-08-30/"
    f"run_id={APPLICATION_RUN_ID}/vacancy_id={VACANCY_ID}"
)
DEFAULT_COLLECTED_AT = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)
DEFAULT_LAST_MODIFIED = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[Any, S3ObjectRef]] = {}
        self.extra_keys: list[str] = []

    @staticmethod
    def uri(key: str) -> str:
        return f"s3://careerops-raw/_lab/hh/{key}"

    def add(
        self,
        key: str,
        payload: Any,
        *,
        collected_at: datetime | None = DEFAULT_COLLECTED_AT,
        last_modified: datetime | None = DEFAULT_LAST_MODIFIED,
    ) -> None:
        self.objects[key] = (
            payload,
            S3ObjectRef(
                bucket="careerops-raw",
                key=f"_lab/hh/{key}",
                sha256="a" * 64,
                size_bytes=100,
                collected_at=collected_at,
                last_modified=last_modified,
            ),
        )

    async def iter_keys(self, prefix: str = ""):
        for key in sorted(
            key for key in [*self.objects, *self.extra_keys] if key.startswith(prefix)
        ):
            yield key

    def relative_key(self, key: str) -> str:
        if key.startswith("s3://careerops-raw/_lab/hh/"):
            return key.removeprefix("s3://careerops-raw/_lab/hh/")
        if key.startswith("_lab/hh/"):
            return key.removeprefix("_lab/hh/")
        return key.strip("/")

    async def get_json_with_metadata(self, key: str) -> tuple[Any, S3ObjectRef]:
        return self.objects[self.relative_key(key)]


class FakeSink:
    def __init__(self) -> None:
        self.profile_calls: list[dict[str, Any]] = []
        self.resume_calls: list[dict[str, Any]] = []
        self.batch_calls: list[dict[str, Any]] = []
        self.partial_calls: list[dict[str, Any]] = []
        self.vacancy_calls: list[dict[str, Any]] = []
        self.decision_calls: list[dict[str, Any]] = []
        self.application_calls: list[dict[str, Any]] = []
        self.observation_run_calls: list[dict[str, Any]] = []
        self.vacancy_observation_calls: list[dict[str, Any]] = []
        self.evaluation_calls: list[dict[str, Any]] = []
        self.vacancy_ids: dict[str, int] = {}

    async def upsert_source_profile(self, **kwargs: Any) -> int:
        self.profile_calls.append(kwargs)
        return 10

    async def upsert_resume(self, **kwargs: Any) -> int:
        self.resume_calls.append(kwargs)
        return 19 + len(self.resume_calls)

    async def upsert_batch_run(self, **kwargs: Any) -> UUID:
        self.batch_calls.append(kwargs)
        return kwargs["run_id"]

    async def upsert_partial_vacancy(self, **kwargs: Any) -> int:
        self.partial_calls.append(kwargs)
        source_id = kwargs["source_entity_id"]
        return self.vacancy_ids.setdefault(source_id, 100 + len(self.vacancy_ids))

    async def upsert_vacancy(self, **kwargs: Any) -> int:
        self.vacancy_calls.append(kwargs)
        source_id = kwargs["vacancy"].source_entity_id
        return self.vacancy_ids[source_id]

    async def upsert_vacancy_decision(self, **kwargs: Any) -> int:
        self.decision_calls.append(kwargs)
        return 501

    async def upsert_application(self, **kwargs: Any) -> int:
        self.application_calls.append(kwargs)
        return 601

    async def upsert_observation_run(self, **kwargs: Any) -> UUID:
        self.observation_run_calls.append(kwargs)
        return kwargs["run_id"]

    async def upsert_vacancy_observation(self, **kwargs: Any) -> None:
        self.vacancy_observation_calls.append(kwargs)

    async def upsert_evaluation_work_item(self, **kwargs: Any) -> None:
        self.evaluation_calls.append(kwargs)


class IdempotentTransactionalSink(FakeSink):
    """In-memory OLTP model retained only for the existing component-level contracts."""

    def __init__(self, *, fail_on_evaluation_attempt: int | None = None) -> None:
        super().__init__()
        self.profiles: dict[tuple[str, str], int] = {}
        self.resumes: dict[tuple[int, str], int] = {}
        self.vacancies: dict[tuple[str, str], int] = {}
        self.observation_runs: dict[UUID, dict[str, Any]] = {}
        self.vacancy_observations: dict[tuple[UUID, int], dict[str, Any]] = {}
        self.evaluation_items: dict[tuple[UUID, int, int], dict[str, Any]] = {}
        self.fail_on_evaluation_attempt = fail_on_evaluation_attempt
        self.evaluation_attempts = 0

    def snapshot(self) -> tuple[Any, ...]:
        return deepcopy(
            (
                self.profiles,
                self.resumes,
                self.vacancies,
                self.observation_runs,
                self.vacancy_observations,
                self.evaluation_items,
            )
        )

    def restore(self, snapshot: tuple[Any, ...]) -> None:
        (
            self.profiles,
            self.resumes,
            self.vacancies,
            self.observation_runs,
            self.vacancy_observations,
            self.evaluation_items,
        ) = snapshot

    @property
    def row_counts(self) -> tuple[int, int, int, int, int, int]:
        return (
            len(self.profiles),
            len(self.resumes),
            len(self.vacancies),
            len(self.observation_runs),
            len(self.vacancy_observations),
            len(self.evaluation_items),
        )

    async def upsert_source_profile(self, **kwargs: Any) -> int:
        self.profile_calls.append(kwargs)
        key = (kwargs["source"], kwargs["profile_key"])
        return self.profiles.setdefault(key, 10 + len(self.profiles))

    async def upsert_resume(self, **kwargs: Any) -> int:
        self.resume_calls.append(kwargs)
        key = (kwargs["source_profile_id"], kwargs["source_resume_id"])
        return self.resumes.setdefault(key, 20 + len(self.resumes))

    async def upsert_partial_vacancy(self, **kwargs: Any) -> int:
        self.partial_calls.append(kwargs)
        key = (kwargs["source"], kwargs["source_entity_id"])
        return self.vacancies.setdefault(key, 100 + len(self.vacancies))

    async def upsert_vacancy(self, **kwargs: Any) -> int:
        self.vacancy_calls.append(kwargs)
        vacancy = kwargs["vacancy"]
        key = (vacancy.source, vacancy.source_entity_id)
        return self.vacancies.setdefault(key, 100 + len(self.vacancies))

    async def upsert_observation_run(self, **kwargs: Any) -> UUID:
        self.observation_run_calls.append(kwargs)
        run_id = kwargs["run_id"]
        self.observation_runs[run_id] = deepcopy(kwargs)
        return run_id

    async def upsert_vacancy_observation(self, **kwargs: Any) -> None:
        self.vacancy_observation_calls.append(kwargs)
        key = (kwargs["run_id"], kwargs["vacancy_id"])
        self.vacancy_observations[key] = deepcopy(kwargs)

    async def upsert_evaluation_work_item(self, **kwargs: Any) -> None:
        self.evaluation_calls.append(kwargs)
        self.evaluation_attempts += 1
        if self.evaluation_attempts == self.fail_on_evaluation_attempt:
            raise RuntimeError("injected evaluation materialization failure")
        key = (kwargs["run_id"], kwargs["vacancy_id"], kwargs["resume_id"])
        self.evaluation_items[key] = deepcopy(kwargs)


class SnapshotTransaction(AbstractAsyncContextManager[None]):
    def __init__(self, connection: SnapshotConnection) -> None:
        self.connection = connection
        self.snapshot: tuple[Any, ...] | None = None

    async def __aenter__(self) -> None:
        self.snapshot = self.connection.sink.snapshot()
        self.connection.events.append("begin")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_value, traceback
        if exc_type is not None:
            assert self.snapshot is not None
            self.connection.sink.restore(self.snapshot)
            self.connection.events.append("rollback")
        else:
            self.connection.events.append("commit")
        return False


class SnapshotConnection:
    def __init__(self, sink: IdempotentTransactionalSink) -> None:
        self.sink = sink
        self.events: list[str] = []

    def transaction(self) -> SnapshotTransaction:
        return SnapshotTransaction(self)


def wire_payloads(relative_path: str) -> dict[str, Any]:
    """Load a fresh synthetic compatibility catalogue without repairing values."""
    path = Path(__file__).resolve().parents[1] / "fixtures" / "hh" / relative_path
    return json.loads(path.read_text(encoding="utf-8"))


def complete_v2_store() -> FakeStore:
    payloads = wire_payloads("raw_v2/audited_apply.json")
    store = FakeStore()
    store.add(f"{RUN_PREFIX}/run.json", payloads["run"])
    store.add(f"{RUN_PREFIX}/summary.json", payloads["summary"])
    store.add(f"{CANDIDATE_PREFIX}/search_item.json", payloads["search_item"])
    store.add(
        f"{CANDIDATE_PREFIX}/vacancy.json",
        payloads["vacancy"],
        last_modified=datetime(2026, 8, 30, 7, 2, tzinfo=UTC),
    )
    store.add(f"{CANDIDATE_PREFIX}/decision.json", payloads["decision"])
    store.add(f"{CANDIDATE_PREFIX}/outcome.json", payloads["outcome"])
    store.add(
        f"{APPLICATION_PREFIX}/application_request.json",
        payloads["application_request"],
    )
    store.add(
        f"{APPLICATION_PREFIX}/application_result.json",
        payloads["application_result"],
    )
    store.add(
        f"{APPLICATION_PREFIX}/vacancy_before.json",
        payloads["vacancy_before"],
    )
    store.add(
        f"{APPLICATION_PREFIX}/vacancy_after.json",
        payloads["vacancy_after"],
    )
    return store


def v3_run_payload() -> dict[str, Any]:
    return wire_payloads("raw_v3/observe.json")["run"]


def v3_summary() -> dict[str, Any]:
    return wire_payloads("raw_v3/observe.json")["summary"]


def complete_v3_store() -> FakeStore:
    payloads = wire_payloads("raw_v3/observe.json")
    store = FakeStore()
    store.add(f"{RUN_PREFIX}/run.json", payloads["run"])
    store.add(f"{RUN_PREFIX}/summary.json", payloads["summary"])
    store.add(f"{CANDIDATE_PREFIX}/search_item.json", payloads["search_item"])
    store.add(f"{CANDIDATE_PREFIX}/vacancy.json", payloads["vacancy"])
    store.add(f"{CANDIDATE_PREFIX}/observation.json", payloads["observation"])
    store.add(
        f"{CANDIDATE_PREFIX}/evaluation_candidates.json",
        payloads["evaluation_candidates"],
    )
    return store
