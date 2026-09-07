"""Pure durable-queue contracts for CareerOPS Processing v2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from .contracts import ProcessingInputManifest

RECONCILIATION_CANCEL_PREFIX = "reconciliation."
RECONCILIATION_SUPERSEDED = "superseded"
RECONCILIATION_WITHDRAWN = "reconciliation.withdrawn"


def _require_positive_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _require_non_empty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    if value != value.strip():
        raise ValueError(f"{name} must not contain surrounding whitespace")


class ProcessingJobStatus(StrEnum):
    """Operational states shared with careerops_v2.processing_jobs."""

    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    DEFERRED = "deferred"
    RETRYABLE_FAILURE = "retryable_failure"
    SUCCEEDED = "succeeded"
    TERMINAL_FAILURE = "terminal_failure"
    CANCELLED = "cancelled"


class ProcessingJobLeaseLost(RuntimeError):
    """Raised when a stale worker mutates a job it no longer owns."""


@dataclass(frozen=True, slots=True, order=True)
class ProcessingPairKey:
    """Operational PostgreSQL identity of one vacancy × binding decision unit."""

    vacancy_id: int
    binding_id: int

    def __post_init__(self) -> None:
        _require_positive_int(self.vacancy_id, "vacancy_id")
        _require_positive_int(self.binding_id, "binding_id")


@dataclass(frozen=True, slots=True)
class ProcessingWorkSpec:
    """One exact desired vacancy × binding evaluation before durable enqueue."""

    vacancy_id: int
    binding_id: int
    binding_version: int
    input_fingerprint: str
    input_manifest_uri: str
    pipeline_version: str
    policy_version: str

    @classmethod
    def from_manifest(
        cls,
        *,
        vacancy_id: int,
        binding_id: int,
        input_manifest_uri: str,
        manifest: ProcessingInputManifest,
    ) -> ProcessingWorkSpec:
        return cls(
            vacancy_id=vacancy_id,
            binding_id=binding_id,
            binding_version=manifest.binding.binding_version,
            input_fingerprint=manifest.input_fingerprint(),
            input_manifest_uri=input_manifest_uri,
            pipeline_version=manifest.versions.pipeline_version,
            policy_version=manifest.target_policy.policy_version,
        )

    def __post_init__(self) -> None:
        _require_positive_int(self.vacancy_id, "vacancy_id")
        _require_positive_int(self.binding_id, "binding_id")
        _require_positive_int(self.binding_version, "binding_version")
        if len(self.input_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in self.input_fingerprint
        ):
            raise ValueError("input_fingerprint must be a lowercase SHA-256 hex digest")
        _require_non_empty(self.input_manifest_uri, "input_manifest_uri")
        if not self.input_manifest_uri.startswith("s3://"):
            raise ValueError("input_manifest_uri must use s3://")
        _require_non_empty(self.pipeline_version, "pipeline_version")
        _require_non_empty(self.policy_version, "policy_version")

    @property
    def pair(self) -> ProcessingPairKey:
        return ProcessingPairKey(self.vacancy_id, self.binding_id)


@dataclass(frozen=True, slots=True)
class ProcessingJobRecord:
    """One claimed Processing job with its current fencing lease."""

    id: UUID
    vacancy_id: int
    binding_id: int
    binding_version: int
    input_fingerprint: str
    input_manifest_uri: str
    pipeline_version: str
    policy_version: str
    status: ProcessingJobStatus
    attempt_count: int
    lease_owner: str | None
    lease_token: UUID | None
    leased_at: datetime | None
    lease_expires_at: datetime | None

    @property
    def pair(self) -> ProcessingPairKey:
        return ProcessingPairKey(self.vacancy_id, self.binding_id)


class ProcessingJobStore(Protocol):
    """Durable queue capability owned by Processing orchestration."""

    async def reconcile_current(self, spec: ProcessingWorkSpec) -> UUID: ...

    async def withdraw_pair(
        self,
        pair: ProcessingPairKey,
        *,
        reason: str = RECONCILIATION_WITHDRAWN,
    ) -> int: ...

    async def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> ProcessingJobRecord | None: ...

    async def mark_running(self, job: ProcessingJobRecord) -> None: ...

    async def renew_lease(
        self,
        job: ProcessingJobRecord,
        *,
        lease_seconds: int = 300,
    ) -> None: ...

    async def succeed(self, job: ProcessingJobRecord, *, result_artifact_uri: str) -> None: ...

    async def defer(
        self,
        job: ProcessingJobRecord,
        *,
        error_category: str,
        next_attempt_at: datetime,
    ) -> None: ...

    async def retryable_failure(
        self,
        job: ProcessingJobRecord,
        *,
        error_category: str,
        next_attempt_at: datetime,
    ) -> None: ...

    async def terminal_failure(
        self,
        job: ProcessingJobRecord,
        *,
        error_category: str,
    ) -> None: ...

    async def cancel(self, job: ProcessingJobRecord, *, reason: str = "operator.cancelled") -> None: ...
