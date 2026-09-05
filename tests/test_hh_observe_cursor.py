from __future__ import annotations

from uuid import UUID

import pytest
from support.hh_observe import (
    NOW,
    FakeObserveDriver,
    FakeObserveStore,
    MemoryQueryCursorStore,
    make_reconciliation,
    no_sleep,
)

from careerops_integrations.hh.configuration import DiscoveryConfig, HHAccountConfig
from careerops_integrations.hh.observe import HHObserveRunner
from careerops_integrations.hh.runtime import HHExternalWriteGuard, RuntimeMode


@pytest.mark.asyncio
async def test_observe_rotates_a_bounded_query_window_across_runs() -> None:
    discovery = DiscoveryConfig.model_validate(
        {
            "schema_version": 1,
            "defaults": {
                "max_queries_per_run": 2,
                "search_query_delay_seconds": 0,
                "full_fetch_min_delay_seconds": 0,
                "full_fetch_max_delay_seconds": 0,
            },
            "query_sets": {
                "ml_core": {
                    "version": 1,
                    "queries": [
                        {"key": f"query-{index}", "text": f"Q{index}"}
                        for index in range(1, 6)
                    ],
                }
            },
        }
    )
    account = HHAccountConfig.model_validate(
        {
            "key": "junior",
            "profile": "stable-hh-profile",
            "bindings": [
                {
                    "key": "ml",
                    "source_resume_id": "resume-junior",
                    "target_key": "ml-target",
                    "query_sets": ["ml_core"],
                }
            ],
        }
    )
    reconciliation = make_reconciliation(account)
    cursor = MemoryQueryCursorStore()
    driver = FakeObserveDriver(
        {
            f"Q{index}": [{"items": [], "page": 0, "pages": 1}]
            for index in range(1, 6)
        }
    )
    store = FakeObserveStore()
    run_ids = (
        UUID("81000000-0000-4000-8000-000000000001"),
        UUID("81000000-0000-4000-8000-000000000002"),
        UUID("81000000-0000-4000-8000-000000000003"),
        UUID("81000000-0000-4000-8000-000000000004"),
    )

    results = []
    for run_id in run_ids:
        results.append(
            await HHObserveRunner(
                driver=driver,
                store=store,
                account=account,
                discovery=discovery,
                resume_reconciliation=reconciliation,
                query_cursor_store=cursor,
                external_write_guard=HHExternalWriteGuard(
                    RuntimeMode.OBSERVE,
                    False,
                ),
                sleep=no_sleep,
                clock=lambda: NOW,
            ).run(run_id=run_id)
        )

    assert driver.search_calls == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q1", "Q2", "Q3"]
    assert [reservation.window_start for reservation in cursor.reservations] == [
        0,
        2,
        4,
        1,
    ]
    assert [reservation.next_query_offset for reservation in cursor.reservations] == [
        2,
        4,
        1,
        3,
    ]
    assert [result.summary["queries_selected"] for result in results] == [2, 2, 2, 2]
    assert results[2].summary["query_rotation_wrapped"] is True
    for result in results:
        run_payload = store.objects[f"{result.run_prefix}/run.json"]
        assert run_payload["max_queries_per_run"] == 2
        assert len(run_payload["query_keys"]) == 2
