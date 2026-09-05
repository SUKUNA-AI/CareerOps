from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from support.s3 import JsonWriteRef

from careerops_integrations.hh.configuration import DiscoveryConfig, HHAccountConfig
from careerops_integrations.hh.driver import HHVacancySearchPage
from careerops_integrations.hh.observe import ObserveQueryCursorReservation
from careerops_integrations.hh.resume_sync import (
    AccountResumeInventory,
    ReconciledResume,
    ResumeLifecycle,
    ResumeReconciliationResult,
)

NOW = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)


class FakeObserveStore:
    def __init__(self) -> None:
        self.objects: dict[str, Any] = {}
        self.collected_at: dict[str, datetime | None] = {}

    async def put_json(
        self,
        key: str,
        payload: Any,
        *,
        collected_at: datetime | None = None,
    ) -> JsonWriteRef:
        self.objects[key] = deepcopy(payload)
        self.collected_at[key] = collected_at
        return JsonWriteRef(uri=f"s3://careerops-raw/_lab/hh/{key}")


class FakeObserveDriver:
    def __init__(self, pages: dict[str, list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.search_calls: list[str] = []
        self.fetch_calls: list[str] = []
        self.resume_fetch_calls = 0
        self.submit_calls = 0

    def search_vacancy_pages(self, *, text: str, **kwargs: Any):
        del kwargs
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
        del resume_id
        self.resume_fetch_calls += 1
        raise AssertionError("OBSERVE must not fetch resume content")

    def submit_application(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
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


def make_discovery() -> DiscoveryConfig:
    return DiscoveryConfig.model_validate(
        {
            "schema_version": 1,
            "defaults": {
                "pages": 2,
                "per_page": 100,
                "search_query_delay_seconds": 0,
                "full_fetch_min_delay_seconds": 0,
                "full_fetch_max_delay_seconds": 0,
            },
            "query_sets": {
                "ml_core": {
                    "version": 1,
                    "queries": [{"key": "ml-query", "text": "ML"}],
                },
                "python_backend_core": {
                    "version": 1,
                    "queries": [{"key": "backend-query", "text": "Backend"}],
                },
            },
        }
    )


def make_account(key: str = "junior") -> HHAccountConfig:
    return HHAccountConfig.model_validate(
        {
            "key": key,
            "profile": f"profile-{key}",
            "bindings": [
                {
                    "key": "ml",
                    "source_resume_id": f"resume-{key}",
                    "target_key": "ml-target",
                    "auto_apply": False,
                    "binding_version": 4,
                    "query_sets": ["ml_core", "python_backend_core"],
                }
            ],
        }
    )


def make_reconciliation(account: HHAccountConfig) -> ResumeReconciliationResult:
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


def make_pages() -> dict[str, list[dict[str, Any]]]:
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
