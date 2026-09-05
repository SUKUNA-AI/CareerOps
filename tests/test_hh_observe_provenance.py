from __future__ import annotations

from copy import deepcopy
from uuid import UUID

import pytest
from support.hh_observe import (
    NOW,
    FakeObserveDriver,
    FakeObserveStore,
    MemoryQueryCursorStore,
    make_account,
    make_discovery,
    make_pages,
    make_reconciliation,
    no_sleep,
)

from careerops_integrations.hh.observe import HHObserveRunner
from careerops_integrations.hh.runtime import HHExternalWriteGuard, RuntimeMode


@pytest.mark.asyncio
async def test_observe_preserves_source_payload_purity_and_raw_pages() -> None:
    account = make_account()
    pages = make_pages()
    source_pages = deepcopy(pages)
    store = FakeObserveStore()
    result = await HHObserveRunner(
        driver=FakeObserveDriver(pages),
        store=store,
        account=account,
        discovery=make_discovery(),
        resume_reconciliation=make_reconciliation(account),
        query_cursor_store=MemoryQueryCursorStore(),
        external_write_guard=HHExternalWriteGuard(RuntimeMode.OBSERVE, False),
        sleep=no_sleep,
        clock=lambda: NOW,
    ).run(run_id=UUID("22222222-2222-4222-8222-222222222222"))

    raw_ml_key = (
        f"{result.run_prefix}/discovery/queries/query=ml-query/page=000.json"
    )
    raw_backend_key = (
        f"{result.run_prefix}/discovery/queries/query=backend-query/page=000.json"
    )
    assert store.objects[raw_ml_key] == source_pages["ML"][0]
    assert store.objects[raw_backend_key] == source_pages["Backend"][0]
    assert store.collected_at[raw_ml_key] == NOW

    source_keys = [
        key
        for key in store.objects
        if key.endswith(("page=000.json", "search_item.json", "vacancy.json"))
    ]
    forbidden = {
        "run_id",
        "account_key",
        "target_key",
        "collected_at",
        "query_key",
        "producer",
    }
    for key in source_keys:
        assert forbidden.isdisjoint(store.objects[key])
        assert store.collected_at[key] == NOW

    names = {key.rsplit("/", 1)[-1] for key in store.objects}
    assert "decision.json" not in names
    assert "cover_letter.json" not in names
    assert "outcome.json" not in names
    assert "application_request.json" not in names
    observation = store.objects[
        f"{result.run_prefix}/candidates/vacancy_id=1/observation.json"
    ]
    assert observation["candidate_source_resume_ids"] == ["resume-junior"]
    assert observation["candidate_target_keys"] == ["ml-target"]
    assert observation["candidate_bindings"][0]["binding_version"] == 4
    evaluation_sidecar = store.objects[
        f"{result.run_prefix}/candidates/vacancy_id=1/evaluation_candidates.json"
    ]
    assert evaluation_sidecar["evaluation_semantics"] == (
        "routing_only_no_relevance_decision"
    )
    assert evaluation_sidecar["evaluations"][0]["duplicate_key"] == {
        "account_key": "junior",
        "source_profile": "profile-junior",
        "source_resume_id": "resume-junior",
        "vacancy_id": "1",
    }
    assert evaluation_sidecar["evaluations"][0]["evaluation_status"] == (
        "pending_filtering_v2"
    )
