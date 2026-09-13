"""Таксономия ошибок на границе HH source adapter

Adapter сообщает только transport/source failures
Retry scheduling и account orchestration остаются ответственностью source-task worker
"""

from __future__ import annotations

from enum import StrEnum


class HHFailureKind(StrEnum):
    """Стабильные категории ошибок, которые публикует HH adapter"""

    AUTH_REQUIRED = "auth_required"
    SESSION_EXPIRED = "session_expired"
    CAPTCHA_REQUIRED = "captcha_required"
    RATE_LIMITED = "rate_limited"
    TEMPORARY_HTTP_ERROR = "temporary_http_error"
    PERMANENT_SOURCE_ERROR = "permanent_source_error"
    UNKNOWN_RESPONSE = "unknown_response"


class HHFailureDisposition(StrEnum):
    """Консервативное действие по умолчанию для persistent source task"""

    RETRY = "retry"
    DEFER = "defer"
    BLOCK_ACCOUNT = "block_account"
    TERMINAL = "terminal"


class HHTransportError(RuntimeError):
    """Ошибка source/transport без протекания vendor exception types наружу"""

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
    """Возвращает fail-safe действие по умолчанию для source worker"""

    dispositions = {
        HHFailureKind.AUTH_REQUIRED: HHFailureDisposition.BLOCK_ACCOUNT,
        HHFailureKind.SESSION_EXPIRED: HHFailureDisposition.BLOCK_ACCOUNT,
        HHFailureKind.CAPTCHA_REQUIRED: HHFailureDisposition.DEFER,
        HHFailureKind.RATE_LIMITED: HHFailureDisposition.DEFER,
        HHFailureKind.TEMPORARY_HTTP_ERROR: HHFailureDisposition.RETRY,
        HHFailureKind.PERMANENT_SOURCE_ERROR: HHFailureDisposition.TERMINAL,
        HHFailureKind.UNKNOWN_RESPONSE: HHFailureDisposition.DEFER,
    }
    return dispositions[kind]
