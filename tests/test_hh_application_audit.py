from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from careerops_integrations.hh.application_audit import (
    HHApplicationAuditService,
    HHApplicationBlocked,
    HHApplicationUncertain,
)
from careerops_integrations.hh.application_claims import (
    ApplicationClaimAcquisition,
    ApplicationClaimRecord,
    ApplicationClaimStatus,
    ApplicationIdentity,
)
from careerops_integrations.hh.runtime import HHExternalWriteGuard, RuntimeMode

WRITE_GUARD = HHExternalWriteGuard(
    runtime_mode=RuntimeMode.APPLY,
    allow_external_writes=True,
)


@dataclass
class _Ref:
    uri: str
    sha256: str = "a" * 64


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
    ) -> _Ref:
        self.objects[key] = payload
        self.collected_at[key] = collected_at
        return _Ref(uri=f"s3://careerops-raw/_lab/hh/{key}")


class MemoryClaimStore:
    """Small atomic test double with the same safe-retry ownership rules."""

    def __init__(self) -> None:
        self.records: dict[ApplicationIdentity, ApplicationClaimRecord] = {}
        self.prepared_vacancies: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}
        self.prepare_calls = 0
        self._lock = asyncio.Lock()

    async def prepare_identity(
        self,
        *,
        identity: ApplicationIdentity,
        account_key: str,
        vacancy: dict[str, Any],
        observed_at: datetime,
        raw_uri: str,
        content_hash: str,
    ) -> None:
        del account_key, observed_at, raw_uri, content_hash
        self.prepare_calls += 1
        self.prepared_vacancies[(identity.source_profile, identity.vacancy_id)] = dict(
            vacancy
        )

    async def acquire(
        self,
        *,
        identity: ApplicationIdentity,
        account_key: str,
        application_run_id: UUID,
        claimed_at: datetime,
    ) -> ApplicationClaimAcquisition:
        async with self._lock:
            assert (
                identity.source_profile,
                identity.vacancy_id,
            ) in self.prepared_vacancies
            current = self.records.get(identity)
            if (
                current is not None
                and current.status is not ApplicationClaimStatus.FAILED_SAFE_TO_RETRY
            ):
                return ApplicationClaimAcquisition(False, current)
            record = ApplicationClaimRecord(
                identity=identity,
                account_key=account_key,
                application_run_id=application_run_id,
                status=ApplicationClaimStatus.CLAIMED,
                attempt_count=1 if current is None else current.attempt_count + 1,
                claimed_at=claimed_at,
                state_changed_at=claimed_at,
            )
            self.records[identity] = record
            return ApplicationClaimAcquisition(True, record)

    async def transition(
        self,
        *,
        identity: ApplicationIdentity,
        application_run_id: UUID,
        expected: tuple[ApplicationClaimStatus, ...],
        status: ApplicationClaimStatus,
        changed_at: datetime,
        error_type: str | None = None,
        error_message: str | None = None,
        upstream_evidence: dict[str, Any] | None = None,
    ) -> ApplicationClaimRecord:
        del error_type, error_message, upstream_evidence
        async with self._lock:
            current = self.records[identity]
            if current.application_run_id != application_run_id:
                raise AssertionError("claim owner mismatch")
            if current.status not in expected:
                raise AssertionError(
                    f"unexpected transition {current.status.value} -> {status.value}"
                )
            updated = replace(
                current,
                status=status,
                state_changed_at=changed_at,
            )
            self.records[identity] = updated
            return updated


class FakeDriver:
    def __init__(
        self,
        before: dict[str, Any],
        *,
        existing_pairs: set[tuple[str, str]] | None = None,
    ) -> None:
        self.before = before
        self.existing_pairs = set(existing_pairs or ())
        self.submitted_pairs: set[tuple[str, str]] = set()
        self.submit_calls = 0
        self.mode: str | None = None
        self.submit_error: Exception | None = None
        self.evidence_error: Exception | None = None

    def fetch_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        return dict(self.before)

    def find_application_evidence(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
    ) -> dict[str, Any]:
        if self.evidence_error is not None:
            raise self.evidence_error
        pair = (resume_id, vacancy_id)
        found = pair in self.existing_pairs or pair in self.submitted_pairs
        return {
            "source_profile": "careerops-ml",
            "source_resume_id": resume_id,
            "vacancy_id": vacancy_id,
            "found": found,
            "negotiation_ids": ["negotiation-1"] if found else [],
        }

    def _submit(self, *, resume_id: str, vacancy_id: str, mode: str) -> None:
        self.submit_calls += 1
        self.mode = mode
        if self.submit_error is not None:
            raise self.submit_error
        self.submitted_pairs.add((resume_id, vacancy_id))

    def submit_application(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> dict[str, Any]:
        del message
        self._submit(
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            mode="negotiations_api",
        )
        return {}

    def submit_application_with_test(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> dict[str, Any]:
        del message
        self._submit(
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            mode="upstream_hh_test",
        )
        return {"success": "true"}


def _vacancy(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": "123",
        "name": "ML Engineer",
        "relations": [],
        "archived": False,
        "closed_for_applicants": False,
        "has_test": False,
        "response_url": None,
        "employer": {"name": "Example"},
        "alternate_url": "https://hh.ru/vacancy/123",
    }
    payload.update(overrides)
    return payload


def _service(
    *,
    driver: FakeDriver,
    store: FakeStore | None = None,
    claims: MemoryClaimStore | None = None,
    context: dict[str, Any] | None = None,
    account_key: str = "ml_3y",
) -> HHApplicationAuditService:
    return HHApplicationAuditService(
        driver=driver,
        store=store or FakeStore(),
        claim_store=claims or MemoryClaimStore(),
        account_key=account_key,
        profile_id="careerops-ml",
        external_write_guard=WRITE_GUARD,
        application_context=context,
    )


@pytest.mark.asyncio
async def test_audited_application_persists_four_objects_and_exact_evidence() -> None:
    store = FakeStore()
    driver = FakeDriver(_vacancy())
    claims = MemoryClaimStore()
    service = _service(
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
    store = FakeStore()
    driver = FakeDriver(_vacancy(has_test=True))
    result = await _service(driver=driver, store=store).apply(
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
    service = _service(driver=FakeDriver(_vacancy(**overrides)))

    with pytest.raises(HHApplicationBlocked):
        await service.apply(
            vacancy_id="123",
            resume_id="resume",
            message="hello",
        )


@pytest.mark.asyncio
async def test_global_relation_for_first_resume_does_not_block_second_resume() -> None:
    driver = FakeDriver(
        _vacancy(relations=["got_response"]),
        existing_pairs={("resume-first", "123")},
    )

    result = await _service(driver=driver).apply(
        vacancy_id="123",
        resume_id="resume-second",
        message="hello",
    )

    assert result.confirmed is True
    assert driver.submitted_pairs == {("resume-second", "123")}


@pytest.mark.asyncio
async def test_existing_exact_resume_vacancy_pair_is_blocked_without_post() -> None:
    driver = FakeDriver(
        _vacancy(),
        existing_pairs={("resume", "123")},
    )
    claims = MemoryClaimStore()

    with pytest.raises(HHApplicationBlocked, match="already exists"):
        await _service(driver=driver, claims=claims).apply(
            vacancy_id="123",
            resume_id="resume",
            message="hello",
        )

    assert driver.submit_calls == 0
    record = next(iter(claims.records.values()))
    assert record.status is ApplicationClaimStatus.SUBMITTED


@pytest.mark.asyncio
async def test_persistent_claim_blocks_sequential_duplicate() -> None:
    driver = FakeDriver(_vacancy())
    claims = MemoryClaimStore()
    service = _service(driver=driver, claims=claims)

    await service.apply(vacancy_id="123", resume_id="resume", message="hello")
    with pytest.raises(HHApplicationBlocked, match="persistent claim"):
        await service.apply(vacancy_id="123", resume_id="resume", message="hello")

    assert driver.submit_calls == 1
    assert claims.prepare_calls == 2
    assert len(claims.prepared_vacancies) == 1


@pytest.mark.asyncio
async def test_apply_materializes_missing_vacancy_before_claim_and_posts_once() -> None:
    driver = FakeDriver(_vacancy())
    claims = MemoryClaimStore()

    assert claims.prepared_vacancies == {}
    result = await _service(driver=driver, claims=claims).apply(
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
    driver = FakeDriver(_vacancy())
    claims = MemoryClaimStore()

    await _service(driver=driver, claims=claims).apply(
        vacancy_id="123",
        resume_id="resume-first",
        message="first",
    )
    await _service(driver=driver, claims=claims).apply(
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
    driver = FakeDriver(_vacancy())
    claims = MemoryClaimStore()

    await _service(
        driver=driver,
        claims=claims,
        account_key="junior",
    ).apply(vacancy_id="123", resume_id="resume", message="hello")

    with pytest.raises(HHApplicationBlocked, match="persistent claim"):
        await _service(
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
    driver = FakeDriver(_vacancy())
    claims = MemoryClaimStore()
    first = _service(driver=driver, claims=claims)
    second = _service(driver=driver, claims=claims)

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
    driver = FakeDriver(_vacancy())
    driver.submit_error = TimeoutError("connection lost after request write")
    claims = MemoryClaimStore()
    service = _service(driver=driver, claims=claims)

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
    driver = FakeDriver(_vacancy())
    driver.evidence_error = RuntimeError("negotiations lookup failed")
    claims = MemoryClaimStore()

    with pytest.raises(HHApplicationUncertain) as error:
        await _service(driver=driver, claims=claims).apply(
            vacancy_id="123",
            resume_id="resume",
            message="hello",
        )

    assert error.value.external_write_attempted is False
    assert driver.submit_calls == 0
    record = next(iter(claims.records.values()))
    assert record.status is ApplicationClaimStatus.UNCERTAIN


@pytest.mark.asyncio
async def test_pre_fetched_before_avoids_duplicate_initial_fetch() -> None:
    class CountingDriver(FakeDriver):
        def __init__(self, before: dict[str, Any]) -> None:
            super().__init__(before)
            self.fetch_count = 0

        def fetch_vacancy(self, vacancy_id: str) -> dict[str, Any]:
            self.fetch_count += 1
            return super().fetch_vacancy(vacancy_id)

    before = _vacancy()
    driver = CountingDriver(before)
    result = await _service(driver=driver).apply(
        vacancy_id="123",
        resume_id="resume",
        message="hello",
        before=before,
    )

    assert result.confirmed is True
    assert driver.fetch_count == 1
