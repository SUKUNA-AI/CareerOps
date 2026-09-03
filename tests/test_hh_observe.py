from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from careerops_integrations.hh.configuration import (
    DiscoveryConfig,
    HHAccountConfig,
)
from careerops_integrations.hh.driver import HHVacancySearchPage
from careerops_integrations.hh.observe import (
    HHObserveRunner,
    ObserveQueryCursorReservation,
)
from careerops_integrations.hh.resume_sync import (
    AccountResumeInventory,
    ReconciledResume,
    ResumeLifecycle,
    ResumeReconciliationResult,
)
from careerops_integrations.hh.runtime import HHExternalWriteGuard, RuntimeMode

NOW = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Ref:
    uri: str


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, Any] = {}
        self.collected_at: dict[str, datetime | None] = {}

    async def put_json(
        self,
        key: str,
        payload: Any,
        *,
        collected_at: datetime | None = None,
    ) -> Ref:
        self.objects[key] = deepcopy(payload)
        self.collected_at[key] = collected_at
        return Ref(uri=f"s3://careerops-raw/_lab/hh/{key}")


class FakeDriver:
    def __init__(self, pages: dict[str, list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.search_calls: list[str] = []
        self.fetch_calls: list[str] = []
        self.resume_fetch_calls = 0
        self.submit_calls = 0

    def search_vacancy_pages(self, *, text: str, **kwargs: Any):
        self.search_calls.append(text)
        return [
            HHVacancySearchPage(page=index, payload=deepcopy(payload))
            for index, payload in enumerate(self.pages[text])
        ]

    def fetch_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        self.fetch_calls.append(vacancy_id)
        return {
            "id": vacancy_id,
            "name": f"Full {vacancy_id}",
            "relations": ["got_response"] if vacancy_id == "1" else [],
            "description": "military drone DevOps Senior Lead",
        }

    def fetch_resume(self, resume_id: str) -> dict[str, Any]:
        self.resume_fetch_calls += 1
        raise AssertionError("OBSERVE must not fetch resume content")

    def submit_application(self, **kwargs: Any) -> dict[str, Any]:
        self.submit_calls += 1
        raise AssertionError("OBSERVE must not submit")


async def no_sleep(seconds: float) -> None:
    assert seconds >= 0


class MemoryQueryCursorStore:
    """Profile-keyed test double matching the PostgreSQL rotation semantics."""

    def __init__(self) -> None:
        self.state: dict[str, tuple[str, int, int]] = {}
        self.reservations: list[ObserveQueryCursorReservation] = []

    async def reserve(
        self,
        *,
        source_profile: str,
        account_key: str,
        catalog_signature: str,
        catalog_size: int,
        max_queries: int,
        run_id: UUID,
        reserved_at: datetime,
    ) -> ObserveQueryCursorReservation:
        del run_id, reserved_at
        current = self.state.get(source_profile)
        start = (
            current[2]
            if current is not None
            and current[:2] == (catalog_signature, catalog_size)
            else 0
        )
        window_size = min(catalog_size, max_queries)
        next_offset = (start + window_size) % catalog_size
        self.state[source_profile] = (
            catalog_signature,
            catalog_size,
            next_offset,
        )
        reservation = ObserveQueryCursorReservation(
            source_profile=source_profile,
            account_key=account_key,
            catalog_signature=catalog_signature,
            catalog_size=catalog_size,
            window_start=start,
            window_size=window_size,
            next_query_offset=next_offset,
        )
        self.reservations.append(reservation)
        return reservation


def _discovery() -> DiscoveryConfig:
    return DiscoveryConfig.model_validate(
        {
            "schema_version": 1,
            "defaults": {
                "area": 1,
                "period": 14,
                "pages": 2,
                "per_page": 100,
                "search_query_delay_seconds": 0,
                "full_fetch_min_delay_seconds": 0,
                "full_fetch_max_delay_seconds": 0,
            },
            "query_sets": {
                "ml_core": {
                    "version": 1,
                    "queries": [
                        {"key": "ml-query", "text": "ML", "enabled": True}
                    ],
                },
                "python_backend_core": {
                    "version": 1,
                    "queries": [
                        {
                            "key": "backend-query",
                            "text": "Backend",
                            "enabled": True,
                        }
                    ],
                },
            },
        }
    )


def _account(key: str = "junior") -> HHAccountConfig:
    return HHAccountConfig.model_validate(
        {
            "key": key,
            "profile": f"profile-{key}",
            "enabled": True,
            "observe_runs_per_day": 3,
            "apply_daily_cap": 100,
            "bindings": [
                {
                    "key": "ml",
                    "source_resume_id": f"resume-{key}",
                    "target_key": "ml-target",
                    "enabled": True,
                    "auto_apply": False,
                    "binding_version": 4,
                    "query_sets": ["ml_core", "python_backend_core"],
                }
            ],
        }
    )


def _reconciliation(account: HHAccountConfig) -> ResumeReconciliationResult:
    resume = ReconciledResume(
        source_profile=account.profile,
        source_resume_id=f"resume-{account.key}",
        current_title="Configured",
        lifecycle=ResumeLifecycle.ACTIVE,
        first_seen_at=NOW,
        last_seen_at=NOW,
        binding_key="ml",
        binding_enabled=True,
        target_key="ml-target",
        query_sets=("ml_core", "python_backend_core"),
        auto_apply=False,
        binding_version=4,
        content_sha256="a" * 64,
        source_payload={"id": f"resume-{account.key}", "title": "Configured"},
    )
    return ResumeReconciliationResult(
        inventory=AccountResumeInventory(
            account_key=account.key,
            source_profile=account.profile,
            reconciled_at=NOW,
            resumes=(resume,),
        ),
        registered_ids=(resume.source_resume_id,),
        updated_ids=(),
        reactivated_ids=(),
        deleted_ids=(),
    )


def _pages() -> dict[str, list[dict[str, Any]]]:
    return {
        "ML": [
            {
                "items": [
                    {
                        "id": "1",
                        "name": "Senior DevOps AI Platform",
                        "relations": ["got_response"],
                        "professional_roles": [{"id": "unrelated"}],
                    },
                    {"id": "2", "name": "Lead Drone ML Engineer"},
                ],
                "page": 0,
                "pages": 1,
            }
        ],
        "Backend": [
            {
                "items": [
                    {"id": "1", "name": "Backend Python alternate search item"},
                    {
                        "id": "3",
                        "name": "Military relations vacancy",
                        "relations": ["discarded"],
                    },
                ],
                "page": 0,
                "pages": 1,
            }
        ],
    }


@pytest.mark.asyncio
async def test_observe_unions_deduplicates_and_full_fetches_without_filtering() -> None:
    account = _account()
    store = FakeStore()
    driver = FakeDriver(_pages())
    result = await HHObserveRunner(
        driver=driver,
        store=store,
        account=account,
        discovery=_discovery(),
        resume_reconciliation=_reconciliation(account),
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
async def test_observe_preserves_source_payload_purity_and_raw_pages() -> None:
    account = _account()
    pages = _pages()
    source_pages = deepcopy(pages)
    store = FakeStore()
    result = await HHObserveRunner(
        driver=FakeDriver(pages),
        store=store,
        account=account,
        discovery=_discovery(),
        resume_reconciliation=_reconciliation(account),
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


@pytest.mark.asyncio
async def test_observe_keeps_successful_earlier_page_when_later_page_fails() -> None:
    class PartialFailureDriver(FakeDriver):
        def search_vacancy_pages(self, *, text: str, **kwargs: Any):
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

    account = _account()
    store = FakeStore()
    result = await HHObserveRunner(
        driver=PartialFailureDriver(_pages()),
        store=store,
        account=account,
        discovery=_discovery(),
        resume_reconciliation=_reconciliation(account),
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
async def test_observe_persists_independent_vacancy_resume_evaluation_pairs() -> None:
    account = HHAccountConfig.model_validate(
        {
            "key": "junior",
            "profile": "profile-junior",
            "enabled": True,
            "observe_runs_per_day": 3,
            "apply_daily_cap": 100,
            "bindings": [
                {
                    "key": "ml",
                    "source_resume_id": "resume-ml",
                    "target_key": "ml-target",
                    "auto_apply": False,
                    "query_sets": ["ml_core"],
                },
                {
                    "key": "backend",
                    "source_resume_id": "resume-backend",
                    "target_key": "backend-target",
                    "auto_apply": False,
                    "query_sets": ["python_backend_core"],
                },
            ],
        }
    )
    resumes = tuple(
        ReconciledResume(
            source_profile=account.profile,
            source_resume_id=binding.source_resume_id,
            current_title=binding.key,
            lifecycle=ResumeLifecycle.ACTIVE,
            first_seen_at=NOW,
            last_seen_at=NOW,
            binding_key=binding.key,
            binding_enabled=True,
            target_key=binding.target_key,
            query_sets=binding.query_sets,
            auto_apply=False,
            binding_version=binding.binding_version,
            content_sha256=("a" if binding.key == "ml" else "b") * 64,
            source_payload={"id": binding.source_resume_id, "title": binding.key},
        )
        for binding in account.bindings
    )
    reconciliation = ResumeReconciliationResult(
        inventory=AccountResumeInventory(
            account_key=account.key,
            source_profile=account.profile,
            reconciled_at=NOW,
            resumes=resumes,
        ),
        registered_ids=tuple(resume.source_resume_id for resume in resumes),
        updated_ids=(),
        reactivated_ids=(),
        deleted_ids=(),
    )
    store = FakeStore()
    result = await HHObserveRunner(
        driver=FakeDriver(_pages()),
        store=store,
        account=account,
        discovery=_discovery(),
        resume_reconciliation=reconciliation,
        query_cursor_store=MemoryQueryCursorStore(),
        external_write_guard=HHExternalWriteGuard(RuntimeMode.OBSERVE, False),
        sleep=no_sleep,
        clock=lambda: NOW,
    ).run(run_id=UUID("55555555-5555-4555-8555-555555555555"))

    sidecar = store.objects[
        f"{result.run_prefix}/candidates/vacancy_id=1/evaluation_candidates.json"
    ]
    evaluations = sidecar["evaluations"]
    assert [item["source_resume_id"] for item in evaluations] == [
        "resume-ml",
        "resume-backend",
    ]
    assert {
        (
            item["duplicate_key"]["account_key"],
            item["duplicate_key"]["source_profile"],
            item["duplicate_key"]["source_resume_id"],
            item["duplicate_key"]["vacancy_id"],
        )
        for item in evaluations
    } == {
        ("junior", "profile-junior", "resume-ml", "1"),
        ("junior", "profile-junior", "resume-backend", "1"),
    }
    assert result.summary["evaluation_candidate_count"] == 6

    ml_only_sidecar = store.objects[
        f"{result.run_prefix}/candidates/vacancy_id=2/evaluation_candidates.json"
    ]
    backend_evaluation = next(
        item
        for item in ml_only_sidecar["evaluations"]
        if item["source_resume_id"] == "resume-backend"
    )
    assert backend_evaluation["matched_query_keys"] == ["ml-query"]
    assert backend_evaluation["matched_query_sets"] == ["ml_core"]
    assert backend_evaluation["resume_query_sets"] == ["python_backend_core"]
    assert backend_evaluation["provenance_overlap"] == {
        "has_overlap": False,
        "matched_query_keys": [],
        "matched_query_sets": [],
    }


@pytest.mark.asyncio
async def test_same_vacancy_is_not_deduplicated_across_account_runs() -> None:
    store = FakeStore()
    prefixes: list[str] = []
    for account_key, run_id in (
        ("first", UUID("33333333-3333-4333-8333-333333333333")),
        ("second", UUID("44444444-4444-4444-8444-444444444444")),
    ):
        account = _account(account_key)
        result = await HHObserveRunner(
            driver=FakeDriver(_pages()),
            store=store,
            account=account,
            discovery=_discovery(),
            resume_reconciliation=_reconciliation(account),
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


@pytest.mark.asyncio
async def test_observe_enforces_unique_and_full_fetch_technical_bounds() -> None:
    account = _account()
    discovery = _discovery()
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
    driver = FakeDriver(_pages())
    store = FakeStore()

    result = await HHObserveRunner(
        driver=driver,
        store=store,
        account=account,
        discovery=discovery,
        resume_reconciliation=_reconciliation(account),
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


@pytest.mark.asyncio
async def test_observe_rotates_a_bounded_query_window_across_runs() -> None:
    discovery = DiscoveryConfig.model_validate(
        {
            "schema_version": 1,
            "defaults": {
                "pages": 1,
                "per_page": 50,
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
    reconciliation = _reconciliation(account)
    cursor = MemoryQueryCursorStore()
    driver = FakeDriver(
        {
            f"Q{index}": [{"items": [], "page": 0, "pages": 1}]
            for index in range(1, 6)
        }
    )
    store = FakeStore()
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
