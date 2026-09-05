from __future__ import annotations

import asyncio

import pytest
from support.hh_application_audit import (
    FakeApplicationDriver,
    MemoryClaimStore,
    make_audit_service,
    make_audit_vacancy,
)

from careerops_integrations.hh.application_audit import (
    HHApplicationBlocked,
    HHApplicationUncertain,
)
from careerops_integrations.hh.application_claims import (
    ApplicationClaimStatus,
    ApplicationIdentity,
)


@pytest.mark.asyncio
async def test_persistent_claim_blocks_sequential_duplicate() -> None:
    driver = FakeApplicationDriver(make_audit_vacancy())
    claims = MemoryClaimStore()
    service = make_audit_service(driver=driver, claims=claims)

    await service.apply(vacancy_id="123", resume_id="resume", message="hello")
    with pytest.raises(HHApplicationBlocked, match="persistent claim"):
        await service.apply(vacancy_id="123", resume_id="resume", message="hello")

    assert driver.submit_calls == 1
    assert claims.prepare_calls == 2
    assert len(claims.prepared_vacancies) == 1


@pytest.mark.asyncio
async def test_apply_materializes_missing_vacancy_before_claim_and_posts_once() -> None:
    driver = FakeApplicationDriver(make_audit_vacancy())
    claims = MemoryClaimStore()

    assert claims.prepared_vacancies == {}
    result = await make_audit_service(driver=driver, claims=claims).apply(
        vacancy_id="123",
        resume_id="resume",
        message="hello",
    )

    assert result.claim_status is ApplicationClaimStatus.SUBMITTED
    assert ("careerops-ml", "123") in claims.prepared_vacancies
    assert len(claims.records) == 1
    assert driver.submit_calls == 1


@pytest.mark.asyncio
async def test_same_vacancy_has_one_materialization_and_resume_specific_claims() -> None:
    driver = FakeApplicationDriver(make_audit_vacancy())
    claims = MemoryClaimStore()

    await make_audit_service(driver=driver, claims=claims).apply(
        vacancy_id="123",
        resume_id="resume-first",
        message="first",
    )
    await make_audit_service(driver=driver, claims=claims).apply(
        vacancy_id="123",
        resume_id="resume-second",
        message="second",
    )

    assert len(claims.prepared_vacancies) == 1
    assert len(claims.records) == 2
    assert driver.submitted_pairs == {
        ("resume-first", "123"),
        ("resume-second", "123"),
    }
    assert driver.submit_calls == 2


@pytest.mark.asyncio
async def test_account_label_rename_does_not_create_a_new_claim_identity() -> None:
    driver = FakeApplicationDriver(make_audit_vacancy())
    claims = MemoryClaimStore()

    await make_audit_service(
        driver=driver,
        claims=claims,
        account_key="junior",
    ).apply(vacancy_id="123", resume_id="resume", message="hello")

    with pytest.raises(HHApplicationBlocked, match="persistent claim"):
        await make_audit_service(
            driver=driver,
            claims=claims,
            account_key="junior_main",
        ).apply(vacancy_id="123", resume_id="resume", message="hello")

    assert driver.submit_calls == 1
    assert len(claims.records) == 1
    record = next(iter(claims.records.values()))
    assert record.account_key == "junior"
    assert record.identity == ApplicationIdentity(
        source_profile="careerops-ml",
        source_resume_id="resume",
        vacancy_id="123",
    )


@pytest.mark.asyncio
async def test_atomic_claim_allows_only_one_concurrent_post() -> None:
    driver = FakeApplicationDriver(make_audit_vacancy())
    claims = MemoryClaimStore()
    first = make_audit_service(driver=driver, claims=claims)
    second = make_audit_service(driver=driver, claims=claims)

    results = await asyncio.gather(
        first.apply(vacancy_id="123", resume_id="resume", message="one"),
        second.apply(vacancy_id="123", resume_id="resume", message="two"),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, HHApplicationBlocked) for result in results) == 1
    assert driver.submit_calls == 1


@pytest.mark.asyncio
async def test_ambiguous_post_is_uncertain_and_never_blindly_retried() -> None:
    driver = FakeApplicationDriver(make_audit_vacancy())
    driver.submit_error = TimeoutError("connection lost after request write")
    claims = MemoryClaimStore()
    service = make_audit_service(driver=driver, claims=claims)

    with pytest.raises(HHApplicationUncertain) as first_error:
        await service.apply(vacancy_id="123", resume_id="resume", message="hello")
    assert first_error.value.external_write_attempted is True

    with pytest.raises(HHApplicationBlocked, match="status=UNCERTAIN"):
        await service.apply(vacancy_id="123", resume_id="resume", message="hello")

    assert driver.submit_calls == 1
    record = next(iter(claims.records.values()))
    assert record.status is ApplicationClaimStatus.UNCERTAIN


@pytest.mark.asyncio
async def test_uncertain_precheck_blocks_post_and_persists_uncertain_claim() -> None:
    driver = FakeApplicationDriver(make_audit_vacancy())
    driver.evidence_error = RuntimeError("negotiations lookup failed")
    claims = MemoryClaimStore()

    with pytest.raises(HHApplicationUncertain) as error:
        await make_audit_service(driver=driver, claims=claims).apply(
            vacancy_id="123",
            resume_id="resume",
            message="hello",
        )

    assert error.value.external_write_attempted is False
    assert driver.submit_calls == 0
    record = next(iter(claims.records.values()))
    assert record.status is ApplicationClaimStatus.UNCERTAIN
