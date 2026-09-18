from __future__ import annotations

from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Protocol
from uuid import UUID

from .domain import (
    ApplicationLease,
    ApplicationStateView,
    TransportPrecheck,
    TransportSubmitResult,
)


class ApplicantTransport(Protocol):
    async def precheck(
        self,
        *,
        account_key: str,
        vacancy_id: str,
        resume_id: str,
        cover_letter: str,
    ) -> TransportPrecheck: ...

    async def submit(
        self,
        *,
        account_key: str,
        vacancy_id: str,
        resume_id: str,
        cover_letter: str,
        questionnaire_answers: Mapping[str, str],
    ) -> TransportSubmitResult: ...

    async def find_submission(
        self,
        *,
        account_key: str,
        vacancy_id: str,
        resume_id: str,
    ) -> str | None: ...


class ApplicationAuditStore(Protocol):
    async def write_event(
        self,
        *,
        application_id: UUID,
        event: str,
        payload: Mapping[str, object],
    ) -> str: ...


class ApplicationRepository(Protocol):
    async def recover_expired_leases(self) -> int: ...
    async def claim_next(
        self, *, worker_id: str, lease_seconds: int
    ) -> ApplicationLease | None: ...
    async def claim_reconciliation(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> ApplicationLease | None: ...
    async def mark_submitting(self, lease: ApplicationLease, *, audit_uri: str) -> bool: ...
    async def mark_submitted_unconfirmed(
        self,
        lease: ApplicationLease,
        *,
        audit_uri: str,
        reconcile_after_seconds: int,
    ) -> None: ...
    async def mark_confirmed(
        self,
        lease: ApplicationLease,
        *,
        audit_uri: str,
    ) -> None: ...
    async def mark_safe_failure(
        self,
        lease: ApplicationLease,
        *,
        reason_code: str,
        audit_uri: str,
        retry_after_seconds: int,
    ) -> None: ...
    async def mark_uncertain(
        self,
        lease: ApplicationLease,
        *,
        reason_code: str,
        audit_uri: str,
        reconcile_after_seconds: int,
    ) -> None: ...
    async def mark_blocked(
        self,
        lease: ApplicationLease,
        *,
        reason_code: str,
        audit_uri: str,
    ) -> None: ...
    async def reschedule_reconciliation(
        self,
        lease: ApplicationLease,
        *,
        audit_uri: str,
        reconcile_after_seconds: int,
    ) -> None: ...
    async def get_state(self, application_id: UUID) -> ApplicationStateView | None: ...


class ApplicationUnitOfWork(Protocol):
    applications: ApplicationRepository

    async def __aenter__(self) -> ApplicationUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


ApplicationUnitOfWorkFactory = Callable[[], ApplicationUnitOfWork]
