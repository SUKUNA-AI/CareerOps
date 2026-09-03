"""Persistent identity and state machine for exactly-once HH write attempts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID


class ApplicationClaimStatus(StrEnum):
    """Durable states controlling whether an external HH write may run."""

    CLAIMED = "CLAIMED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    UNCERTAIN = "UNCERTAIN"
    FAILED_SAFE_TO_RETRY = "FAILED_SAFE_TO_RETRY"


@dataclass(frozen=True, slots=True)
class ApplicationIdentity:
    """Stable upstream profile + resume + vacancy identity for one application.

    ``account_key`` is intentionally excluded: it is a mutable CareerOPS label,
    while the HH profile and entity identifiers are the canonical upstream identity.
    PostgreSQL further resolves this natural key to ``resume_id + vacancy_id``.
    """

    source_profile: str
    source_resume_id: str
    vacancy_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "source_profile",
            "source_resume_id",
            "vacancy_id",
        ):
            value = getattr(self, field_name)
            normalized = value.strip()
            if not normalized:
                raise ValueError(f"{field_name} must not be empty")
            object.__setattr__(self, field_name, normalized)


@dataclass(frozen=True, slots=True)
class ApplicationClaimRecord:
    """Current durable claim returned by an atomic persistence operation."""

    identity: ApplicationIdentity
    account_key: str
    application_run_id: UUID
    status: ApplicationClaimStatus
    attempt_count: int
    claimed_at: datetime
    state_changed_at: datetime


@dataclass(frozen=True, slots=True)
class ApplicationClaimAcquisition:
    """Report whether this worker owns the claim or observed an existing one."""

    acquired: bool
    record: ApplicationClaimRecord


class ApplicationClaimStore(Protocol):
    """Atomic durable boundary required before any external application POST."""

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
        """Ensure reconciled resume and authoritative vacancy OLTP identities exist."""

        ...

    async def acquire(
        self,
        *,
        identity: ApplicationIdentity,
        account_key: str,
        application_run_id: UUID,
        claimed_at: datetime,
    ) -> ApplicationClaimAcquisition:
        """Acquire a new/safe-retry claim or return the blocking current claim."""

        ...

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
        """Atomically move the owning claim through an allowed state transition."""

        ...


class ApplicationClaimConflict(RuntimeError):
    """Signal that another attempt already owns or finalized an identity."""


class ApplicationClaimTransitionError(RuntimeError):
    """Signal a stale or otherwise invalid claim transition."""


class ApplicationClaimIdentityNotMaterialized(RuntimeError):
    """Signal that the canonical resume/vacancy OLTP rows do not exist yet."""
