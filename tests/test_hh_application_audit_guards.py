from __future__ import annotations

from typing import Any

import pytest
from support.hh_application_audit import (
    FakeApplicationDriver,
    MemoryClaimStore,
    make_audit_service,
    make_audit_vacancy,
)

from careerops_integrations.hh.application_audit import HHApplicationBlocked
from careerops_integrations.hh.application_claims import ApplicationClaimStatus


@pytest.mark.parametrize(
    "overrides",
    [
        {"archived": True},
        {"closed_for_applicants": True},
        {"response_url": "https://example.com/apply"},
    ],
)
@pytest.mark.asyncio
async def test_structural_guards_block_application(overrides: dict[str, Any]) -> None:
    service = make_audit_service(
        driver=FakeApplicationDriver(make_audit_vacancy(**overrides))
    )

    with pytest.raises(HHApplicationBlocked):
        await service.apply(
            vacancy_id="123",
            resume_id="resume",
            message="hello",
        )


@pytest.mark.asyncio
async def test_global_relation_for_first_resume_does_not_block_second_resume() -> None:
    driver = FakeApplicationDriver(
        make_audit_vacancy(relations=["got_response"]),
        existing_pairs={("resume-first", "123")},
    )

    result = await make_audit_service(driver=driver).apply(
        vacancy_id="123",
        resume_id="resume-second",
        message="hello",
    )

    assert result.confirmed is True
    assert driver.submitted_pairs == {("resume-second", "123")}


@pytest.mark.asyncio
async def test_existing_exact_resume_vacancy_pair_is_blocked_without_post() -> None:
    driver = FakeApplicationDriver(
        make_audit_vacancy(),
        existing_pairs={("resume", "123")},
    )
    claims = MemoryClaimStore()

    with pytest.raises(HHApplicationBlocked, match="already exists"):
        await make_audit_service(driver=driver, claims=claims).apply(
            vacancy_id="123",
            resume_id="resume",
            message="hello",
        )

    assert driver.submit_calls == 0
    record = next(iter(claims.records.values()))
    assert record.status is ApplicationClaimStatus.SUBMITTED
