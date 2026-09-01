from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from careerops_etl.hh_s3_to_postgres import (
    discover_hh_batches,
    discover_hh_candidates,
    load_batch_header,
    load_batch_start,
    load_hh_batch,
)
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
        self.vacancy_ids: dict[str, int] = {}

    async def upsert_source_profile(self, **kwargs: Any) -> int:
        self.profile_calls.append(kwargs)
        return 10

    async def upsert_resume(self, **kwargs: Any) -> int:
        self.resume_calls.append(kwargs)
        return 20

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


def _run_payload() -> dict[str, Any]:
    return {
        "event_type": "hh.batch.started",
        "schema_version": 2,
        "run_id": str(RUN_ID),
        "profile_id": "careerops-ml",
        "resume_id": "resume-123",
        "search": "ML Engineer",
        "area": 1,
        "period": 14,
        "pages": 1,
        "per_page": 50,
        "professional_roles": [96, 165],
        "max_responses": 25,
        "cover_letter_mode": "vacancy_template_v1",
        "live": True,
        "started_at": "2026-08-30T10:00:00+03:00",
    }


def _search_item() -> dict[str, Any]:
    return {
        "id": VACANCY_ID,
        "name": "Junior ML Engineer",
        "employer": {"id": "4233", "name": "Example"},
        "area": {"id": "1", "name": "Москва"},
        "alternate_url": f"https://hh.ru/vacancy/{VACANCY_ID}",
        "published_at": "2026-08-29T12:30:00+0300",
    }


def _vacancy() -> dict[str, Any]:
    return {
        **_search_item(),
        "description": "<p>Python and SQL</p>",
        "salary": {"from": 100000, "to": 150000, "currency": "RUR"},
        "experience": {"id": "between1And3"},
        "schedule": {"id": "remote"},
        "employment": {"id": "full"},
        "relations": [],
        "has_test": False,
        "response_url": None,
        "response_letter_required": False,
        "archived": False,
        "closed_for_applicants": False,
    }


def _summary() -> dict[str, Any]:
    return {
        "event_type": "hh.batch.finished",
        "schema_version": 2,
        "run_id": str(RUN_ID),
        "live": True,
        "discovered": 1,
        "prefiltered": 0,
        "full_fetched": 1,
        "accepted": 1,
        "submitted": 1,
        "confirmed": 1,
        "failed": 0,
        "stopped_on_captcha": False,
        "reasons": {"accepted": 1},
        "finished_at": "2026-08-30T10:05:00+03:00",
        "s3_prefix": RUN_PREFIX,
    }


def _complete_store() -> FakeStore:
    store = FakeStore()
    store.add(f"{RUN_PREFIX}/run.json", _run_payload())
    store.add(f"{RUN_PREFIX}/summary.json", _summary())
    store.add(f"{CANDIDATE_PREFIX}/search_item.json", _search_item())
    store.add(
        f"{CANDIDATE_PREFIX}/vacancy.json",
        _vacancy(),
        last_modified=datetime(2026, 8, 30, 7, 2, tzinfo=UTC),
    )
    store.add(
        f"{CANDIDATE_PREFIX}/decision.json",
        {
            "event_type": "hh.vacancy.decision",
            "schema_version": 2,
            "stage": "full_vacancy_validation",
            "run_id": str(RUN_ID),
            "vacancy_id": VACANCY_ID,
            "vacancy_title": "Junior ML Engineer",
            "company_name": "Example",
            "submission_mode": "negotiations_api",
            "has_test": False,
            "accepted": True,
            "reason": "accepted",
            "matched_domains": ["ml"],
            "blocked_terms": [],
            "search_item_uri": store.uri(f"{CANDIDATE_PREFIX}/search_item.json"),
            "vacancy_uri": store.uri(f"{CANDIDATE_PREFIX}/vacancy.json"),
            "created_at": "2026-08-30T10:02:01+03:00",
        },
    )
    result_key = f"{APPLICATION_PREFIX}/application_result.json"
    store.add(
        f"{CANDIDATE_PREFIX}/outcome.json",
        {
            "event_type": "hh.batch.application_completed",
            "schema_version": 2,
            "run_id": str(RUN_ID),
            "vacancy_id": VACANCY_ID,
            "status": "submitted",
            "confirmed": True,
            "submission_mode": "negotiations_api",
            "application_run_id": str(APPLICATION_RUN_ID),
            "application_result_uri": store.uri(result_key),
            "cover_letter_uri": store.uri(f"{CANDIDATE_PREFIX}/cover_letter.json"),
            "created_at": "2026-08-30T10:04:00+03:00",
        },
    )
    store.add(
        f"{APPLICATION_PREFIX}/application_request.json",
        {
            "event_type": "hh.application.requested",
            "schema_version": 2,
            "run_id": str(APPLICATION_RUN_ID),
            "profile_id": "careerops-ml",
            "resume_id": "resume-123",
            "vacancy_id": VACANCY_ID,
            "vacancy_title": "Junior ML Engineer",
            "company_name": "Example",
            "message": "hello",
            "submission_mode": "negotiations_api",
            "has_test": False,
            "requested_at": "2026-08-30T10:03:00+03:00",
        },
    )
    store.add(
        result_key,
        {
            "event_type": "hh.application.submitted",
            "schema_version": 2,
            "run_id": str(APPLICATION_RUN_ID),
            "profile_id": "careerops-ml",
            "resume_id": "resume-123",
            "vacancy_id": VACANCY_ID,
            "submission_mode": "negotiations_api",
            "status": "submitted",
            "confirmed": True,
            "relations": ["got_response"],
            "upstream_response": {"ignored_raw": True},
            "finished_at": "2026-08-30T10:03:05+03:00",
        },
    )
    store.add(f"{APPLICATION_PREFIX}/vacancy_before.json", _vacancy())
    after = _vacancy()
    after["relations"] = ["got_response"]
    store.add(f"{APPLICATION_PREFIX}/vacancy_after.json", after)
    return store


@pytest.mark.asyncio
async def test_discovers_batch_and_candidate_objects_from_actual_layout() -> None:
    store = _complete_store()
    batches = await discover_hh_batches(store)
    assert len(batches) == 1
    assert batches[0].run_id == RUN_ID
    assert batches[0].has_summary is True

    candidates = await discover_hh_candidates(store, batches[0])
    assert len(candidates) == 1
    assert candidates[0].vacancy_id == VACANCY_ID
    assert candidates[0].search_item_key is not None
    assert candidates[0].vacancy_key is not None
    assert candidates[0].decision_key is not None
    assert candidates[0].outcome_key is not None


@pytest.mark.asyncio
async def test_loads_header_and_normalizes_time_to_utc() -> None:
    store = _complete_store()
    location = (await discover_hh_batches(store))[0]
    batch = await load_batch_start(store, location)
    assert batch.started_at.hour == 7
    assert batch.started_at.utcoffset() is not None
    assert batch.started_at.utcoffset().total_seconds() == 0

    sink = FakeSink()
    header = await load_batch_header(store, sink, location)
    assert header.source_profile_id == 10
    assert header.resume_id == 20
    assert sink.profile_calls == [{"source": "hh", "profile_key": "careerops-ml"}]
    assert sink.resume_calls[0]["source_resume_id"] == "resume-123"
    assert sink.batch_calls[0]["professional_roles"] == ["96", "165"]
    assert sink.batch_calls[0]["status"] == "incomplete"


@pytest.mark.asyncio
async def test_loads_full_batch_and_finalizes_summary() -> None:
    store = _complete_store()
    sink = FakeSink()
    result = await load_hh_batch(store, sink, (await discover_hh_batches(store))[0])

    assert result.complete is True
    assert result.candidates == 1
    assert result.decisions == 1
    assert result.applications == 1
    assert sink.partial_calls[0]["title"] == "Junior ML Engineer"
    assert sink.partial_calls[0]["raw_uri"].endswith("/search_item.json")
    assert sink.partial_calls[0]["observed_at"] == datetime(
        2026, 8, 30, 8, 0, tzinfo=UTC
    )
    canonical = sink.vacancy_calls[0]["vacancy"]
    assert canonical.source_entity_id == VACANCY_ID
    assert canonical.description == "Python and SQL"
    assert canonical.collected_at == datetime(2026, 8, 30, 8, 0, tzinfo=UTC)
    assert sink.vacancy_calls[0]["operational"].vacancy_id == VACANCY_ID

    decision = sink.decision_calls[0]
    assert decision["stage"] == "full_vacancy_validation"
    assert decision["metadata"]["company_name"] == "Example"
    assert "message" not in decision["metadata"]

    application = sink.application_calls[0]
    assert application["application_run_id"] == APPLICATION_RUN_ID
    assert application["batch_run_id"] == RUN_ID
    assert application["upstream_metadata"] == {"relations": ["got_response"]}
    assert "upstream_response" not in application["upstream_metadata"]

    assert [call["status"] for call in sink.batch_calls] == ["incomplete", "finished"]
    assert sink.batch_calls[-1]["discovered"] == 1
    assert sink.batch_calls[-1]["finished_at"].hour == 7


@pytest.mark.asyncio
async def test_legacy_raw_without_metadata_falls_back_to_last_modified() -> None:
    store = _complete_store()
    for key in (
        f"{CANDIDATE_PREFIX}/search_item.json",
        f"{CANDIDATE_PREFIX}/vacancy.json",
    ):
        payload, ref = store.objects[key]
        store.objects[key] = (payload, replace(ref, collected_at=None))

    sink = FakeSink()
    await load_hh_batch(store, sink, (await discover_hh_batches(store))[0])

    assert sink.partial_calls[0]["observed_at"] == datetime(
        2026, 8, 30, 10, 0, tzinfo=UTC
    )
    assert sink.vacancy_calls[0]["vacancy"].collected_at == datetime(
        2026, 8, 30, 7, 2, tzinfo=UTC
    )


@pytest.mark.asyncio
async def test_batch_without_summary_remains_incomplete() -> None:
    store = _complete_store()
    del store.objects[f"{RUN_PREFIX}/summary.json"]
    sink = FakeSink()
    result = await load_hh_batch(store, sink, (await discover_hh_batches(store))[0])

    assert result.complete is False
    assert len(sink.batch_calls) == 1
    assert sink.batch_calls[0]["status"] == "incomplete"
    assert "discovered" not in sink.batch_calls[0]


@pytest.mark.asyncio
async def test_failed_batch_outcome_is_not_invented_as_application() -> None:
    store = _complete_store()
    store.add(
        f"{CANDIDATE_PREFIX}/outcome.json",
        {
            "event_type": "hh.batch.application_failed",
            "schema_version": 2,
            "run_id": str(RUN_ID),
            "vacancy_id": VACANCY_ID,
            "status": "failed",
            "reason": "application_failed",
            "error_type": "RuntimeError",
            "error": "opaque producer failure",
            "created_at": "2026-08-30T10:04:00+03:00",
        },
    )
    sink = FakeSink()
    result = await load_hh_batch(store, sink, (await discover_hh_batches(store))[0])
    assert result.applications == 0
    assert sink.application_calls == []


@pytest.mark.parametrize(
    ("key", "field", "bad_value", "message"),
    [
        (f"{RUN_PREFIX}/run.json", "run_id", str(UUID(int=0)), "run_id mismatch"),
        (
            f"{CANDIDATE_PREFIX}/search_item.json",
            "id",
            "different-vacancy",
            "vacancy_id mismatch",
        ),
        (
            f"{CANDIDATE_PREFIX}/decision.json",
            "run_id",
            str(UUID(int=0)),
            "run_id mismatch",
        ),
        (
            f"{APPLICATION_PREFIX}/application_request.json",
            "resume_id",
            "different-resume",
            "profile/resume mismatch",
        ),
    ],
)
@pytest.mark.asyncio
async def test_rejects_mismatched_path_and_payload_ids(
    key: str,
    field: str,
    bad_value: str,
    message: str,
) -> None:
    store = _complete_store()
    payload, ref = store.objects[key]
    changed = deepcopy(payload)
    changed[field] = bad_value
    store.objects[key] = (changed, ref)
    sink = FakeSink()

    with pytest.raises(RuntimeError, match=message):
        await load_hh_batch(store, sink, (await discover_hh_batches(store))[0])


@pytest.mark.asyncio
async def test_rejects_malformed_batch_path() -> None:
    store = FakeStore()
    store.extra_keys.append("batches/date=not-a-date/run_id=not-a-uuid/run.json")
    with pytest.raises(RuntimeError, match="malformed HH batch path"):
        await discover_hh_batches(store)
