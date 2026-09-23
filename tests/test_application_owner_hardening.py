from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from uuid import uuid4

import pytest

from careerops_application.domain import (
    ApplicationLease,
    TransportPrecheck,
    TransportPrecheckState,
)
from careerops_application.owner import ApplicationOwner

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class _Repo:
    lease: ApplicationLease | None
    safe_failure_reason: str | None = None
    renewals: int = 0

    async def claim_next(self, *, worker_id: str, lease_seconds: int) -> ApplicationLease | None:
        del worker_id, lease_seconds
        lease, self.lease = self.lease, None
        return lease

    async def renew_lease(self, lease: ApplicationLease, *, lease_seconds: int) -> None:
        del lease, lease_seconds
        self.renewals += 1

    async def mark_safe_failure(
        self,
        lease: ApplicationLease,
        *,
        reason_code: str,
        audit_uri: str,
        retry_after_seconds: int,
    ) -> None:
        del lease, audit_uri, retry_after_seconds
        self.safe_failure_reason = reason_code


class _Uow:
    def __init__(self, repo: _Repo) -> None:
        self.applications = repo

    async def __aenter__(self) -> _Uow:
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


class _Transport:
    submit_called = False

    async def precheck(self, **kwargs: object) -> TransportPrecheck:
        del kwargs
        return TransportPrecheck(
            state=TransportPrecheckState.SAFE_FAILURE,
            reason_code="application.hh_transport_unavailable",
        )

    async def submit(self, **kwargs: object) -> object:
        del kwargs
        self.submit_called = True
        raise AssertionError("submit must not run after a failed precheck")


class _Audit:
    async def write_event(self, **kwargs: object) -> str:
        del kwargs
        return "s3://careerops-artifacts/application-owner/test.json"


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


@pytest.mark.asyncio
async def test_precheck_transport_failure_is_safe_retry_not_submit() -> None:
    repo = _Repo(lease=_lease())
    transport = _Transport()
    owner = ApplicationOwner(
        uow_factory=lambda: _Uow(repo),  # type: ignore[arg-type]
        transport=transport,  # type: ignore[arg-type]
        audit=_Audit(),  # type: ignore[arg-type]
        worker_id="test",
    )

    assert await owner.execute_next() is True
    assert repo.safe_failure_reason == "application.hh_transport_unavailable"
    assert transport.submit_called is False
    assert repo.renewals >= 2


def test_postgres_safety_fences_are_explicit_at_runtime_boundaries() -> None:
    claims = (
        PROJECT_ROOT / "src/careerops_application/infrastructure/postgres_claims.py"
    ).read_text(encoding="utf-8")
    rebind = (
        PROJECT_ROOT / "src/careerops_application/infrastructure/postgres_rebind.py"
    ).read_text(encoding="utf-8")
    repository = (
        PROJECT_ROOT / "src/careerops_application/infrastructure/postgres.py"
    ).read_text(encoding="utf-8")

    assert "application-account:" in claims
    assert "application.hh_limit_exceeded" in claims
    assert "unresolved.status IN" in claims
    assert "active.lease_expires_at > now()" in claims
    assert "ac.processing_job_id = app.processing_job_id" in claims
    assert "mr.processing_job_id = app.processing_job_id" in claims
    assert "app.lease_token IS NULL" in claims

    assert "claim_rebind_candidate" in rebind
    assert "ac.id <> %(old_candidate_id)s" in rebind
    assert "ac.processing_job_id <> %(old_processing_job_id)s" in rebind
    assert "mr.processing_job_id = ac.processing_job_id" in rebind
    assert "newer.status IN" in rebind
    assert "application.hh_limit_exceeded" in rebind

    assert "claim_rebind_candidate" in repository
    assert "lease_expires_at > now()" in repository
