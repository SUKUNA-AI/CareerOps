from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class TransportPrecheckState(StrEnum):
    READY = "ready"
    ALREADY_SUBMITTED = "already_submitted"
    SAFE_FAILURE = "safe_failure"
    BLOCKED = "blocked"


class TransportSubmitState(StrEnum):
    SUBMITTED = "submitted"
    ALREADY_SUBMITTED = "already_submitted"
    SAFE_FAILURE = "safe_failure"
    UNCERTAIN = "uncertain"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class QuestionnaireQuestion:
    question_id: str
    text: str
    options: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class TransportPrecheck:
    state: TransportPrecheckState
    reason_code: str | None = None
    upstream_id: str | None = None
    questions: tuple[QuestionnaireQuestion, ...] = ()


@dataclass(frozen=True, slots=True)
class TransportSubmitResult:
    state: TransportSubmitState
    reason_code: str | None = None
    upstream_id: str | None = None


@dataclass(frozen=True, slots=True)
class ApplicationLease:
    application_id: UUID
    account_id: int
    account_key: str
    source_vacancy_id: str
    resume_id: int
    source_resume_id: str
    candidate_id: UUID
    processing_job_id: UUID
    lease_token: UUID
    attempt_count: int


@dataclass(frozen=True, slots=True)
class ApplicationStateView:
    application_id: UUID
    account_id: int
    source_vacancy_id: str
    source_resume_id: str | None
    status: str
    attempt_count: int
    reason_code: str | None
    audit_uri: str | None
