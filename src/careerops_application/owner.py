from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar
from uuid import UUID

from .domain import (
    ApplicationLease,
    ApplicationStateView,
    TransportPrecheckState,
    TransportSubmitState,
)
from .ports import (
    ApplicantTransport,
    ApplicationAuditStore,
    ApplicationUnitOfWorkFactory,
)

_T = TypeVar("_T")


class ApplicationOwner:
    def __init__(
        self,
        *,
        uow_factory: ApplicationUnitOfWorkFactory,
        transport: ApplicantTransport,
        audit: ApplicationAuditStore,
        worker_id: str,
        lease_seconds: int = 120,
        retry_after_seconds: int = 900,
        reconcile_after_seconds: int = 120,
        operation_timeout_seconds: float = 90.0,
        cover_letter: str = "",
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if operation_timeout_seconds <= 0 or operation_timeout_seconds >= lease_seconds:
            raise ValueError("operation_timeout_seconds must be > 0 and smaller than lease_seconds")
        self._uow_factory = uow_factory
        self._transport = transport
        self._audit = audit
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._retry_after_seconds = retry_after_seconds
        self._reconcile_after_seconds = reconcile_after_seconds
        self._operation_timeout_seconds = operation_timeout_seconds
        self._cover_letter = cover_letter

    async def recover_expired_leases(self) -> int:
        async with self._uow_factory() as uow:
            recovered = await uow.applications.recover_expired_leases()
            await uow.commit()
            return recovered

    async def execute_next(self) -> bool:
        lease = await self._claim_next()
        if lease is None:
            return False

        try:
            precheck = await self._run_external_with_heartbeat(
                lease,
                lambda: self._transport.precheck(
                    account_key=lease.account_key,
                    vacancy_id=lease.source_vacancy_id,
                    resume_id=lease.source_resume_id,
                    cover_letter=self._cover_letter,
                ),
            )
        except TimeoutError:
            timeout_uri = await self._audit.write_event(
                application_id=lease.application_id,
                event="precheck_timeout",
                payload={"reason_code": "application.precheck_operation_timeout"},
            )
            await self._renew_lease(lease)
            await self._mark_safe_failure(
                lease,
                reason_code="application.precheck_operation_timeout",
                audit_uri=timeout_uri,
            )
            return True

        precheck_uri = await self._audit.write_event(
            application_id=lease.application_id,
            event="precheck",
            payload={
                "state": precheck.state,
                "reason_code": precheck.reason_code,
                "upstream_id": precheck.upstream_id,
                "questions": [
                    {
                        "question_id": item.question_id,
                        "text": item.text,
                        "options": list(item.options),
                    }
                    for item in precheck.questions
                ],
            },
        )
        await self._renew_lease(lease)

        if precheck.state is TransportPrecheckState.ALREADY_SUBMITTED:
            await self._mark_confirmed(lease, precheck_uri)
            return True
        if precheck.state is TransportPrecheckState.SAFE_FAILURE:
            await self._mark_safe_failure(
                lease,
                reason_code=precheck.reason_code or "application.precheck_transport_failure",
                audit_uri=precheck_uri,
            )
            return True
        if precheck.state is TransportPrecheckState.BLOCKED:
            await self._mark_blocked(
                lease,
                reason_code=precheck.reason_code or "application.precheck_blocked",
                audit_uri=precheck_uri,
            )
            return True

        if not await self._mark_submitting(lease, precheck_uri):
            stale_uri = await self._audit.write_event(
                application_id=lease.application_id,
                event="candidate_stale_before_submit",
                payload={"reason_code": "application.candidate_stale_before_submit"},
            )
            await self._renew_lease(lease)
            await self._mark_blocked(
                lease,
                reason_code="application.candidate_stale_before_submit",
                audit_uri=stale_uri,
            )
            return True

        try:
            submit = await self._run_external_with_heartbeat(
                lease,
                lambda: self._transport.submit(
                    account_key=lease.account_key,
                    vacancy_id=lease.source_vacancy_id,
                    resume_id=lease.source_resume_id,
                    cover_letter=self._cover_letter,
                    questionnaire_answers={},
                ),
            )
        except TimeoutError:
            timeout_uri = await self._audit.write_event(
                application_id=lease.application_id,
                event="submit_timeout",
                payload={"reason_code": "application.submit_outcome_unknown"},
            )
            await self._renew_lease(lease)
            await self._mark_uncertain(
                lease,
                reason_code="application.submit_outcome_unknown",
                audit_uri=timeout_uri,
            )
            return True

        submit_uri = await self._audit.write_event(
            application_id=lease.application_id,
            event="submit",
            payload={
                "state": submit.state,
                "reason_code": submit.reason_code,
                "upstream_id": submit.upstream_id,
            },
        )
        await self._renew_lease(lease)

        if submit.state is TransportSubmitState.ALREADY_SUBMITTED:
            await self._mark_confirmed(lease, submit_uri)
        elif submit.state is TransportSubmitState.SUBMITTED:
            await self._mark_submitted_unconfirmed(lease, submit_uri)
        elif submit.state is TransportSubmitState.SAFE_FAILURE:
            await self._mark_safe_failure(
                lease,
                reason_code=submit.reason_code or "application.safe_failure",
                audit_uri=submit_uri,
            )
        elif submit.state is TransportSubmitState.UNCERTAIN:
            await self._mark_uncertain(
                lease,
                reason_code=submit.reason_code or "application.submit_outcome_unknown",
                audit_uri=submit_uri,
            )
        else:
            await self._mark_blocked(
                lease,
                reason_code=submit.reason_code or "application.transport_blocked",
                audit_uri=submit_uri,
            )
        return True

    async def reconcile_next(self) -> bool:
        async with self._uow_factory() as uow:
            lease = await uow.applications.claim_reconciliation(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
            )
            await uow.commit()
        if lease is None:
            return False

        try:
            upstream_id = await self._run_external_with_heartbeat(
                lease,
                lambda: self._transport.find_submission(
                    account_key=lease.account_key,
                    vacancy_id=lease.source_vacancy_id,
                    resume_id=lease.source_resume_id,
                ),
            )
        except TimeoutError:
            upstream_id = None
        audit_uri = await self._audit.write_event(
            application_id=lease.application_id,
            event="reconcile",
            payload={"found": upstream_id is not None, "upstream_id": upstream_id},
        )
        await self._renew_lease(lease)
        if upstream_id is not None:
            await self._mark_confirmed(lease, audit_uri)
        else:
            async with self._uow_factory() as uow:
                await uow.applications.reschedule_reconciliation(
                    lease,
                    audit_uri=audit_uri,
                    reconcile_after_seconds=self._reconcile_after_seconds,
                )
                await uow.commit()
        return True

    async def get_state(self, application_id: UUID) -> ApplicationStateView | None:
        async with self._uow_factory() as uow:
            return await uow.applications.get_state(application_id)

    async def _claim_next(self) -> ApplicationLease | None:
        async with self._uow_factory() as uow:
            lease = await uow.applications.claim_next(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
            )
            await uow.commit()
            return lease

    async def _renew_lease(self, lease: ApplicationLease) -> None:
        async with self._uow_factory() as uow:
            await uow.applications.renew_lease(lease, lease_seconds=self._lease_seconds)
            await uow.commit()

    async def _run_external_with_heartbeat(
        self,
        lease: ApplicationLease,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        await self._renew_lease(lease)
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(lease, stop))
        try:
            async with asyncio.timeout(self._operation_timeout_seconds):
                return await operation()
        finally:
            stop.set()
            await heartbeat

    async def _heartbeat(self, lease: ApplicationLease, stop: asyncio.Event) -> None:
        interval = max(1.0, self._lease_seconds / 3)
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                await self._renew_lease(lease)

    async def _mark_submitting(self, lease: ApplicationLease, audit_uri: str) -> bool:
        async with self._uow_factory() as uow:
            current = await uow.applications.mark_submitting(lease, audit_uri=audit_uri)
            await uow.commit()
            return current

    async def _mark_submitted_unconfirmed(self, lease: ApplicationLease, audit_uri: str) -> None:
        async with self._uow_factory() as uow:
            await uow.applications.mark_submitted_unconfirmed(
                lease,
                audit_uri=audit_uri,
                reconcile_after_seconds=self._reconcile_after_seconds,
            )
            await uow.commit()

    async def _mark_confirmed(self, lease: ApplicationLease, audit_uri: str) -> None:
        async with self._uow_factory() as uow:
            await uow.applications.mark_confirmed(lease, audit_uri=audit_uri)
            await uow.commit()

    async def _mark_safe_failure(
        self,
        lease: ApplicationLease,
        *,
        reason_code: str,
        audit_uri: str,
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.applications.mark_safe_failure(
                lease,
                reason_code=reason_code,
                audit_uri=audit_uri,
                retry_after_seconds=self._retry_after_seconds,
            )
            await uow.commit()

    async def _mark_uncertain(
        self,
        lease: ApplicationLease,
        *,
        reason_code: str,
        audit_uri: str,
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.applications.mark_uncertain(
                lease,
                reason_code=reason_code,
                audit_uri=audit_uri,
                reconcile_after_seconds=self._reconcile_after_seconds,
            )
            await uow.commit()

    async def _mark_blocked(
        self,
        lease: ApplicationLease,
        *,
        reason_code: str,
        audit_uri: str,
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.applications.mark_blocked(
                lease,
                reason_code=reason_code,
                audit_uri=audit_uri,
            )
            await uow.commit()
