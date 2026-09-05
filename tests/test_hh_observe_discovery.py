from __future__ import annotations

import random
from copy import deepcopy
from typing import Any
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

from careerops_integrations.hh.driver import HHVacancySearchPage
from careerops_integrations.hh.observe import HHObserveRunner
from careerops_integrations.hh.runtime import HHExternalWriteGuard, RuntimeMode


@pytest.mark.asyncio
async def test_observe_unions_deduplicates_and_full_fetches_without_filtering() -> None:
    account = make_account()
    store = FakeObserveStore()
    driver = FakeObserveDriver(make_pages())
    result = await HHObserveRunner(
        driver=driver,
        store=store,
        account=account,
        discovery=make_discovery(),
        resume_reconciliation=make_reconciliation(account),
        query_cursor_store=MemoryQueryCursorStore(),
        external_write_guard=HHExternalWriteGuard(RuntimeMode.OBSERVE, False),
        sleep=no_sleep,
        clock=lambda: NOW,
        rng=random.Random(1),
    ).run(run_id=UUID("11111111-1111-4111-8111-111111111111"))

    assert driver.search_calls == ["ML", "Backend"]
    assert driver.fetch_calls == ["1", "2", "3"]
    assert driver.resume_fetch_calls == 0
    assert driver.submit_calls == 0
    assert result.summary["search_observation_count"] == 4
    assert result.summary["unique_vacancy_count"] == 3
    assert result.summary["full_fetch_attempted"] == 3
    assert result.summary["full_fetched"] == 3
    assert result.summary["submitted"] == 0
    assert result.summary["confirmed"] == 0
    assert result.summary["external_writes_attempted"] == 0

    run_prefix = result.run_prefix
    discovery = store.objects[f"{run_prefix}/discovery.json"]
    vacancy_one = next(v for v in discovery["vacancies"] if v["vacancy_id"] == "1")
    assert vacancy_one["matched_query_keys"] == ["ml-query", "backend-query"]
    assert vacancy_one["matched_query_sets"] == [
        "ml_core",
        "python_backend_core",
    ]
    canonical = store.objects[
        f"{run_prefix}/candidates/vacancy_id=1/search_item.json"
    ]
    assert canonical["name"] == "Senior DevOps AI Platform"


@pytest.mark.asyncio
async def test_observe_keeps_successful_earlier_page_when_later_page_fails() -> None:
    class PartialFailureDriver(FakeObserveDriver):
        def search_vacancy_pages(self, *, text: str, **kwargs: Any):
            del kwargs
            self.search_calls.append(text)
            if text != "ML":
                return [
                    HHVacancySearchPage(page=0, payload=deepcopy(self.pages[text][0]))
                ]

            def pages_then_fail():
                yield HHVacancySearchPage(
                    page=0,
                    payload=deepcopy(self.pages[text][0]),
                )
                raise RuntimeError("page 1 transport failure")

            return pages_then_fail()

    account = make_account()
    store = FakeObserveStore()
    result = await HHObserveRunner(
        driver=PartialFailureDriver(make_pages()),
        store=store,
        account=account,
        discovery=make_discovery(),
        resume_reconciliation=make_reconciliation(account),
        query_cursor_store=MemoryQueryCursorStore(),
        external_write_guard=HHExternalWriteGuard(RuntimeMode.OBSERVE, False),
        sleep=no_sleep,
        clock=lambda: NOW,
    ).run(run_id=UUID("66666666-6666-4666-8666-666666666666"))

    raw_first_page = (
        f"{result.run_prefix}/discovery/queries/query=ml-query/page=000.json"
    )
    assert raw_first_page in store.objects
    discovery = store.objects[f"{result.run_prefix}/discovery.json"]
    assert discovery["query_errors"] == [
        {
            "query_key": "ml-query",
            "error_type": "RuntimeError",
            "error": "page 1 transport failure",
            "reason": "search_failed",
        }
    ]
    assert result.summary["full_fetched"] == 3


@pytest.mark.asyncio
async def test_same_vacancy_is_not_deduplicated_across_account_runs() -> None:
    store = FakeObserveStore()
    prefixes: list[str] = []
    for account_key, run_id in (
        ("first", UUID("33333333-3333-4333-8333-333333333333")),
        ("second", UUID("44444444-4444-4444-8444-444444444444")),
    ):
        account = make_account(account_key)
        result = await HHObserveRunner(
            driver=FakeObserveDriver(make_pages()),
            store=store,
            account=account,
            discovery=make_discovery(),
            resume_reconciliation=make_reconciliation(account),
            query_cursor_store=MemoryQueryCursorStore(),
            external_write_guard=HHExternalWriteGuard(RuntimeMode.OBSERVE, False),
            sleep=no_sleep,
            clock=lambda: NOW,
        ).run(run_id=run_id)
        prefixes.append(result.run_prefix)

    for prefix in prefixes:
        assert f"{prefix}/candidates/vacancy_id=1/observation.json" in store.objects
    first_observation = store.objects[
        f"{prefixes[0]}/candidates/vacancy_id=1/observation.json"
    ]
    second_observation = store.objects[
        f"{prefixes[1]}/candidates/vacancy_id=1/observation.json"
    ]
    assert first_observation["account_key"] == "first"
    assert second_observation["account_key"] == "second"
