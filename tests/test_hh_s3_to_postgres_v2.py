from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from support.hh_s3_to_postgres import (
    APPLICATION_PREFIX,
    APPLICATION_RUN_ID,
    CANDIDATE_PREFIX,
    RUN_ID,
    RUN_PREFIX,
    VACANCY_ID,
    FakeSink,
    complete_v2_store,
)

from careerops_etl.hh_s3_to_postgres import discover_hh_batches, load_hh_batch


@pytest.mark.asyncio
async def test_loads_full_batch_and_finalizes_summary() -> None:
    store = complete_v2_store()
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
async def test_completed_application_allows_explicitly_failed_after_snapshot() -> None:
    store = complete_v2_store()
    after_key = f"{APPLICATION_PREFIX}/vacancy_after.json"
    del store.objects[after_key]
    result_key = f"{APPLICATION_PREFIX}/application_result.json"
    payload, ref = store.objects[result_key]
    changed = deepcopy(payload)
    changed["claim_status"] = "SUBMITTED"
    changed["confirmation_evidence"] = {
        "source_resume_id": "resume-123",
        "vacancy_id": VACANCY_ID,
        "found": True,
    }
    changed["confirmation_error"] = {
        "stage": "vacancy_after",
        "error_type": "TimeoutError",
        "error": "snapshot read timed out",
    }
    store.objects[result_key] = (changed, ref)

    sink = FakeSink()
    result = await load_hh_batch(
        store,
        sink,
        (await discover_hh_batches(store))[0],
    )

    assert result.applications == 1
    application = sink.application_calls[0]
    assert application["after_uri"] is None
    assert application["upstream_metadata"]["claim_status"] == "SUBMITTED"
    assert application["upstream_metadata"]["confirmation_evidence"]["found"] is True


@pytest.mark.asyncio
async def test_legacy_raw_without_metadata_falls_back_to_last_modified() -> None:
    store = complete_v2_store()
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
    store = complete_v2_store()
    del store.objects[f"{RUN_PREFIX}/summary.json"]
    sink = FakeSink()
    result = await load_hh_batch(store, sink, (await discover_hh_batches(store))[0])

    assert result.complete is False
    assert len(sink.batch_calls) == 1
    assert sink.batch_calls[0]["status"] == "incomplete"
    assert "discovered" not in sink.batch_calls[0]


@pytest.mark.asyncio
async def test_failed_batch_outcome_is_not_invented_as_application() -> None:
    store = complete_v2_store()
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
    store = complete_v2_store()
    payload, ref = store.objects[key]
    changed = deepcopy(payload)
    changed[field] = bad_value
    store.objects[key] = (changed, ref)
    sink = FakeSink()

    with pytest.raises(RuntimeError, match=message):
        await load_hh_batch(store, sink, (await discover_hh_batches(store))[0])
