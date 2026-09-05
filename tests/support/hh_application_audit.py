from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from careerops_integrations.hh.application_audit import HHApplicationAuditService
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
class AuditRef:
    uri: str
    sha256: str = "a" * 64


class FakeAuditStore:
    def __init__(self) -> None:
        self.objects: dict[str, Any] = {}
        self.collected_at: dict[str, datetime | None] = {}

    async def put_json(
        self,
        key: str,
        payload: Any,
        *,
        collected_at: datetime | None = None,
    ) -> AuditRef:
        self.objects[key] = payload
        self.collected_at[key] = collected_at
        return AuditRef(uri=f"s3://careerops-raw/_lab/hh/{key}")


class MemoryClaimStore:
    """Atomic service-boundary double with the current safe-retry ownership rules."""

    def __init__(self) -> None:
        self.records: dict[ApplicationIdentity, ApplicationClaimRecord] = {}
        self.prepared_vacancies: dict[tuple[str, str], dict[str, Any]] = {}
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


class FakeApplicationDriver:
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
        del vacancy_id
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


def make_audit_vacancy(**overrides: Any) -> dict[str, Any]:
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


def make_audit_service(
    *,
    driver: FakeApplicationDriver,
    store: FakeAuditStore | None = None,
    claims: MemoryClaimStore | None = None,
    context: dict[str, Any] | None = None,
    account_key: str = "ml_3y",
) -> HHApplicationAuditService:
    return HHApplicationAuditService(
        driver=driver,
        store=store or FakeAuditStore(),
        claim_store=claims or MemoryClaimStore(),
        account_key=account_key,
        profile_id="careerops-ml",
        external_write_guard=WRITE_GUARD,
        application_context=context,
    )
