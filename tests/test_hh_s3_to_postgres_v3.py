from __future__ import annotations

import pytest
from support.hh_s3_to_postgres import (
    RUN_PREFIX,
    FakeSink,
    FakeStore,
    complete_v3_store,
    v3_run_payload,
    v3_summary,
)

from careerops_etl.hh_s3_to_postgres import discover_hh_batches, load_hh_batch


@pytest.mark.asyncio
async def test_v3_observation_materializes_all_real_resume_pairs_without_fake_owner() -> None:
    store = complete_v3_store()
    sink = FakeSink()
    result = await load_hh_batch(store, sink, (await discover_hh_batches(store))[0])

    assert result.complete is True
    assert result.candidates == 1
    assert sink.profile_calls == [
        {
            "source": "hh",
            "profile_key": "careerops-junior",
            "account_key": "junior",
        }
    ]
    assert [call["source_resume_id"] for call in sink.resume_calls] == [
        "resume-de",
        "resume-backend",
    ]
    assert [call["status"] for call in sink.observation_run_calls] == [
        "incomplete",
        "finished",
    ]
    assert sink.observation_run_calls[0]["query_catalog_size"] == 2
    assert sink.observation_run_calls[0]["max_queries_per_run"] == 50
    assert sink.observation_run_calls[0]["query_cursor_start"] == 0
    assert sink.observation_run_calls[0]["query_cursor_next"] == 0
    assert sink.vacancy_observation_calls[0]["matched_query_sets"] == [
        "data_engineering_core"
    ]
    assert [call["resume_id"] for call in sink.evaluation_calls] == [20, 21]
    assert [call["has_provenance_overlap"] for call in sink.evaluation_calls] == [
        True,
        False,
    ]


@pytest.mark.asyncio
async def test_v3_observation_safety_fields_are_not_weakly_accepted() -> None:
    store = FakeStore()
    store.add(f"{RUN_PREFIX}/run.json", v3_run_payload())
    summary = v3_summary()
    summary["submitted"] = 1
    store.add(f"{RUN_PREFIX}/summary.json", summary)
    with pytest.raises(ValueError, match="submitted"):
        await load_hh_batch(store, FakeSink(), (await discover_hh_batches(store))[0])
