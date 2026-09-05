from __future__ import annotations

import pytest
from support.hh_s3_to_postgres import (
    RUN_ID,
    VACANCY_ID,
    FakeSink,
    FakeStore,
    complete_v2_store,
)

from careerops_etl.hh_s3_to_postgres import (
    discover_hh_batches,
    discover_hh_candidates,
    load_batch_header,
    load_batch_start,
)


@pytest.mark.asyncio
async def test_discovers_batch_and_candidate_objects_from_actual_layout() -> None:
    store = complete_v2_store()
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
    store = complete_v2_store()
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
async def test_rejects_malformed_batch_path() -> None:
    store = FakeStore()
    store.extra_keys.append("batches/date=not-a-date/run_id=not-a-uuid/run.json")
    with pytest.raises(RuntimeError, match="malformed HH batch path"):
        await discover_hh_batches(store)
