from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from careerops_application.domain import (
    ApplicationLease,
    ApplicationStateView,
    TransportPrecheck,
    TransportPrecheckState,
    TransportSubmitResult,
    TransportSubmitState,
)
from careerops_application.owner import ApplicationOwner


@dataclass
class FakeRepository:
    next_lease: ApplicationLease | None = None
    reconcile_lease: ApplicationLease | None = None
    transitions: list[tuple[str, str | None]] = field(default_factory=list)
    submitting_current: bool = True

    async def recover_expired_leases(self) -> int:
        return 0

    async def claim_next(self, *, worker_id: str, lease_seconds: int) -> ApplicationLease | None:
        del worker_id, lease_seconds
        lease, self.next_lease = self.next_lease, None
        return lease

    async def claim_reconciliation(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> ApplicationLease | None:
        del worker_id, lease_seconds
        lease, self.reconcile_lease = self.reconcile_lease, None
        return lease

    async def mark_submitting(self, lease: ApplicationLease, *, audit_uri: str) -> bool:
        del lease, audit_uri
        if self.submitting_current:
            self.transitions.append(("submitting", None))
        return self.submitting_current

    async def mark_submitted_unconfirmed(
        self,
        lease: ApplicationLease,
        *,
        audit_uri: str,
        reconcile_after_seconds: int,
    ) -> None:
        del lease, audit_uri, reconcile_after_seconds
        self.transitions.append(("submitted_unconfirmed", None))

    async def mark_confirmed(self, lease: ApplicationLease, *, audit_uri: str) -> None:
        del lease, audit_uri
        self.transitions.append(("submitted_confirmed", None))

    async def mark_safe_failure(
        self,
        lease: ApplicationLease,
        *,
        reason_code: str,
        audit_uri: str,
        retry_after_seconds: int,
    ) -> None:
        del lease, audit_uri, retry_after_seconds
        self.transitions.append(("safe_failure", reason_code))

    async def mark_uncertain(
        self,
        lease: ApplicationLease,
        *,
        reason_code: str,
        audit_uri: str,
        reconcile_after_seconds: int,
    ) -> None:
        del lease, audit_uri, reconcile_after_seconds
        self.transitions.append(("uncertain", reason_code))

    async def mark_blocked(
        self,
        lease: ApplicationLease,
        *,
        reason_code: str,
        audit_uri: str,
    ) -> None:
        del lease, audit_uri
        self.transitions.append(("blocked", reason_code))

    async def reschedule_reconciliation(
        self,
        lease: ApplicationLease,
        *,
        audit_uri: str,
        reconcile_after_seconds: int,
    ) -> None:
        del lease, audit_uri, reconcile_after_seconds
        self.transitions.append(("reconciliation_required", None))

    async def get_state(self, application_id: UUID) -> ApplicationStateView | None:
        del application_id
        return None


class FakeUow:
    def __init__(self, repo: FakeRepository) -> None:
        self.applications = repo

    async def __aenter__(self) -> FakeUow:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        return None


@dataclass
class FakeTransport:
    precheck_result: TransportPrecheck
    submit_result: TransportSubmitResult
    found_submission: str | None = None
    submit_calls: int = 0

    async def precheck(
        self,
        *,
        account_key: str,
        vacancy_id: str,
        resume_id: str,
        cover_letter: str,
    ) -> TransportPrecheck:
        del account_key, vacancy_id, resume_id, cover_letter
        return self.precheck_result

    async def submit(
        self,
        *,
        account_key: str,
        vacancy_id: str,
        resume_id: str,
        cover_letter: str,
        questionnaire_answers: Mapping[str, str],
    ) -> TransportSubmitResult:
        del account_key, vacancy_id, resume_id, cover_letter, questionnaire_answers
        self.submit_calls += 1
        return self.submit_result

    async def find_submission(
        self,
        *,
        account_key: str,
        vacancy_id: str,
        resume_id: str,
    ) -> str | None:
        del account_key, vacancy_id, resume_id
        return self.found_submission


class FakeAudit:
    async def write_event(
        self,
        *,
        application_id: UUID,
        event: str,
        payload: Mapping[str, object],
    ) -> str:
        del payload
        return f"s3://careerops-artifacts/application-owner/{application_id}/{event}.json"


def _lease() -> ApplicationLease:
    return ApplicationLease(
        application_id=uuid4(),
        account_id=7,
        account_key="primary",
        source_vacancy_id="123",
        resume_id=11,
        source_resume_id="resume-hash",
        candidate_id=uuid4(),
        processing_job_id=uuid4(),
        lease_token=uuid4(),
        attempt_count=1,
    )


def _owner(repo: FakeRepository, transport: FakeTransport) -> ApplicationOwner:
    return ApplicationOwner(
        uow_factory=lambda: FakeUow(repo),
        transport=transport,
        audit=FakeAudit(),
        worker_id="test-worker",
    )


@pytest.mark.asyncio
async def test_success_is_never_assumed_confirmed_before_reconciliation() -> None:
    repo = FakeRepository(next_lease=_lease())
    transport = FakeTransport(
        precheck_result=TransportPrecheck(state=TransportPrecheckState.READY),
        submit_result=TransportSubmitResult(state=TransportSubmitState.SUBMITTED),
    )

    assert await _owner(repo, transport).execute_next() is True
    assert repo.transitions == [("submitting", None), ("submitted_unconfirmed", None)]
    assert transport.submit_calls == 1


@pytest.mark.asyncio
async def test_existing_upstream_submission_is_confirmed_without_resubmitting() -> None:
    repo = FakeRepository(next_lease=_lease())
    transport = FakeTransport(
        precheck_result=TransportPrecheck(
            state=TransportPrecheckState.ALREADY_SUBMITTED,
            upstream_id="neg-1",
        ),
        submit_result=TransportSubmitResult(state=TransportSubmitState.SUBMITTED),
    )

    assert await _owner(repo, transport).execute_next() is True
    assert repo.transitions == [("submitted_confirmed", None)]
    assert transport.submit_calls == 0


@pytest.mark.asyncio
async def test_questionnaire_or_other_precheck_block_never_calls_submit() -> None:
    repo = FakeRepository(next_lease=_lease())
    transport = FakeTransport(
        precheck_result=TransportPrecheck(
            state=TransportPrecheckState.BLOCKED,
            reason_code="application.questionnaire_required",
        ),
        submit_result=TransportSubmitResult(state=TransportSubmitState.SUBMITTED),
    )

    assert await _owner(repo, transport).execute_next() is True
    assert repo.transitions == [("blocked", "application.questionnaire_required")]
    assert transport.submit_calls == 0


@pytest.mark.asyncio
async def test_stale_candidate_is_blocked_immediately_before_submit() -> None:
    repo = FakeRepository(next_lease=_lease(), submitting_current=False)
    transport = FakeTransport(
        precheck_result=TransportPrecheck(state=TransportPrecheckState.READY),
        submit_result=TransportSubmitResult(state=TransportSubmitState.SUBMITTED),
    )

    assert await _owner(repo, transport).execute_next() is True
    assert repo.transitions == [(
        "blocked",
        "application.candidate_stale_before_submit",
    )]
    assert transport.submit_calls == 0


@pytest.mark.asyncio
async def test_uncertain_submit_goes_to_reconciliation_not_retry() -> None:
    repo = FakeRepository(next_lease=_lease())
    transport = FakeTransport(
        precheck_result=TransportPrecheck(state=TransportPrecheckState.READY),
        submit_result=TransportSubmitResult(
            state=TransportSubmitState.UNCERTAIN,
            reason_code="application.submit_outcome_unknown",
        ),
    )

    assert await _owner(repo, transport).execute_next() is True
    assert repo.transitions == [
        ("submitting", None),
        ("uncertain", "application.submit_outcome_unknown"),
    ]


@pytest.mark.asyncio
async def test_negative_reconciliation_never_blindly_retries() -> None:
    repo = FakeRepository(reconcile_lease=_lease())
    transport = FakeTransport(
        precheck_result=TransportPrecheck(state=TransportPrecheckState.READY),
        submit_result=TransportSubmitResult(state=TransportSubmitState.SUBMITTED),
        found_submission=None,
    )

    assert await _owner(repo, transport).reconcile_next() is True
    assert repo.transitions == [("reconciliation_required", None)]
    assert transport.submit_calls == 0
