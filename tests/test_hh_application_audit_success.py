from __future__ import annotations

from datetime import UTC
from typing import Any

import pytest
from support.hh_application_audit import (
    FakeApplicationDriver,
    FakeAuditStore,
    MemoryClaimStore,
    make_audit_service,
    make_audit_vacancy,
)

from careerops_integrations.hh.application_claims import ApplicationClaimStatus


@pytest.mark.asyncio
async def test_audited_application_persists_four_objects_and_exact_evidence() -> None:
    store = FakeAuditStore()
    driver = FakeApplicationDriver(make_audit_vacancy())
    claims = MemoryClaimStore()
    service = make_audit_service(
        driver=driver,
        store=store,
        claims=claims,
        context={
            "account_key": "ml_3y",
            "resume_key": "ml_3y",
            "target_key": "ml_3y",
            "binding_version": 2,
        },
    )

    result = await service.apply(
        vacancy_id="123",
        resume_id="resume",
        message="hello",
    )

    assert result.confirmed is True
    assert result.status == "submitted"
    assert result.submission_mode == "negotiations_api"
    assert result.claim_status is ApplicationClaimStatus.SUBMITTED
    assert driver.mode == "negotiations_api"
    claim = next(iter(claims.records.values()))
    assert claim.claimed_at.tzinfo is UTC

    names = {key.rsplit("/", 1)[-1] for key in store.objects}
    assert names == {
        "vacancy_before.json",
        "application_request.json",
        "vacancy_after.json",
        "application_result.json",
    }
    snapshots = [
        payload
        for key, payload in store.objects.items()
        if key.endswith(("vacancy_before.json", "vacancy_after.json"))
    ]
    assert all("collected_at" not in payload for payload in snapshots)
    snapshot_times = [
        collected_at
        for key, collected_at in store.collected_at.items()
        if key.endswith(("vacancy_before.json", "vacancy_after.json"))
    ]
    assert all(
        value is not None and value.tzinfo is not None for value in snapshot_times
    )
    request = next(
        payload
        for key, payload in store.objects.items()
        if key.endswith("application_request.json")
    )
    assert request["profile_id"] == "careerops-ml"
    assert request["resume_id"] == "resume"
    assert request["careerops_binding"] == {
        "account_key": "ml_3y",
        "resume_key": "ml_3y",
        "target_key": "ml_3y",
        "binding_version": 2,
    }
    result_payload = next(
        payload
        for key, payload in store.objects.items()
        if key.endswith("application_result.json")
    )
    assert result_payload["confirmation_evidence"] == {
        "source_profile": "careerops-ml",
        "source_resume_id": "resume",
        "vacancy_id": "123",
        "found": True,
        "negotiation_ids": ["negotiation-1"],
    }


@pytest.mark.asyncio
async def test_test_vacancy_uses_upstream_test_executor() -> None:
    store = FakeAuditStore()
    driver = FakeApplicationDriver(make_audit_vacancy(has_test=True))
    result = await make_audit_service(driver=driver, store=store).apply(
        vacancy_id="123",
        resume_id="resume",
        message="hello",
    )

    assert result.confirmed is True
    assert result.submission_mode == "upstream_hh_test"
    assert driver.mode == "upstream_hh_test"
    request = next(
        value
        for key, value in store.objects.items()
        if key.endswith("application_request.json")
    )
    assert request["has_test"] is True
    assert request["submission_mode"] == "upstream_hh_test"


@pytest.mark.asyncio
async def test_pre_fetched_before_avoids_duplicate_initial_fetch() -> None:
    class CountingDriver(FakeApplicationDriver):
        def __init__(self, before: dict[str, Any]) -> None:
            super().__init__(before)
            self.fetch_count = 0

        def fetch_vacancy(self, vacancy_id: str) -> dict[str, Any]:
            self.fetch_count += 1
            return super().fetch_vacancy(vacancy_id)

    before = make_audit_vacancy()
    driver = CountingDriver(before)
    result = await make_audit_service(driver=driver).apply(
        vacancy_id="123",
        resume_id="resume",
        message="hello",
        before=before,
    )

    assert result.confirmed is True
    assert driver.fetch_count == 1
