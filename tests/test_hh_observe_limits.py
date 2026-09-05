from __future__ import annotations

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
async def test_observe_enforces_unique_and_full_fetch_technical_bounds() -> None:
    account = make_account()
    discovery = make_discovery()
    discovery = discovery.model_copy(
        update={
            "defaults": discovery.defaults.model_copy(
                update={
                    "max_unique_vacancies_per_run": 2,
                    "max_full_fetch_per_run": 1,
                }
            )
        }
    )
    driver = FakeObserveDriver(make_pages())
    store = FakeObserveStore()

    result = await HHObserveRunner(
        driver=driver,
        store=store,
        account=account,
        discovery=discovery,
        resume_reconciliation=make_reconciliation(account),
        query_cursor_store=MemoryQueryCursorStore(),
        external_write_guard=HHExternalWriteGuard(RuntimeMode.OBSERVE, False),
        sleep=no_sleep,
        clock=lambda: NOW,
    ).run(run_id=UUID("77777777-7777-4777-8777-777777777777"))

    assert result.summary["unique_vacancy_count"] == 3
    assert result.summary["candidate_count"] == 2
    assert result.summary["full_fetch_attempted"] == 1
    assert result.summary["full_fetched"] == 1
    assert driver.fetch_calls == ["1"]
    discovery_payload = store.objects[f"{result.run_prefix}/discovery.json"]
    assert discovery_payload["truncated_by_max_unique"] is True
    limited = store.objects[
        f"{result.run_prefix}/candidates/vacancy_id=2/observation.json"
    ]
    assert limited["full_fetch_status"] == "technical_limit"
    evaluations = store.objects[
        f"{result.run_prefix}/candidates/vacancy_id=2/evaluation_candidates.json"
    ]
    assert evaluations["evaluations"] == []
