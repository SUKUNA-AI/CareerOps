"""Adapter-level HH failure taxonomy.

The adapter reports transport/source failures. Retry scheduling and account
orchestration remain responsibilities of source-task workers and orchestration.
"""

from __future__ import annotations

from enum import StrEnum


class HHFailureKind(StrEnum):
    """Stable failure categories exposed by the HH adapter boundary."""

    AUTH_REQUIRED = "auth_required"
    SESSION_EXPIRED = "session_expired"
    CAPTCHA_REQUIRED = "captcha_required"
    RATE_LIMITED = "rate_limited"
    APPLICATION_LIMIT_REACHED = "application_limit_reached"
    TEMPORARY_HTTP_ERROR = "temporary_http_error"
    PERMANENT_SOURCE_ERROR = "permanent_source_error"
    UNKNOWN_RESPONSE = "unknown_response"


class HHFailureDisposition(StrEnum):
    """Conservative default action for a persistent source task."""

    RETRY = "retry"
    DEFER = "defer"
    BLOCK_ACCOUNT = "block_account"
    TERMINAL = "terminal"


class HHTransportError(RuntimeError):
    """Expose one source/transport failure without leaking vendor exception types."""

    def __init__(
        self,
        *,
        kind: HHFailureKind,
        operation: str,
        message: str,
    ) -> None:
        super().__init__(f"{operation}: {message}")
        self.kind = kind
        self.operation = operation


def default_failure_disposition(kind: HHFailureKind) -> HHFailureDisposition:
    """Return a fail-safe default; workers may apply stricter source policy later."""

    dispositions = {
        HHFailureKind.AUTH_REQUIRED: HHFailureDisposition.BLOCK_ACCOUNT,
        HHFailureKind.SESSION_EXPIRED: HHFailureDisposition.BLOCK_ACCOUNT,
        HHFailureKind.CAPTCHA_REQUIRED: HHFailureDisposition.DEFER,
        HHFailureKind.RATE_LIMITED: HHFailureDisposition.DEFER,
        HHFailureKind.APPLICATION_LIMIT_REACHED: HHFailureDisposition.DEFER,
        HHFailureKind.TEMPORARY_HTTP_ERROR: HHFailureDisposition.RETRY,
        HHFailureKind.PERMANENT_SOURCE_ERROR: HHFailureDisposition.TERMINAL,
        HHFailureKind.UNKNOWN_RESPONSE: HHFailureDisposition.DEFER,
    }
    return dispositions[kind]
