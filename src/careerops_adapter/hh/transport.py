"""Read-only HH transport boundary for the v2 source adapter.

The initial implementation deliberately wraps the existing pinned
hh-applicant-tool CLI integration instead of duplicating its authentication and
protocol behavior. No filtering, scoring, scheduling, or application policy is
implemented here.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Protocol

from careerops_integrations.hh.driver import (
    HHApplicantToolCLI,
    HHDriverError,
    ParamValue,
)

from .errors import HHFailureKind, HHTransportError

_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS = 3.0


def _request_min_interval_from_env() -> float:
    raw = os.getenv(
        "CAREEROPS_HH_REQUEST_MIN_INTERVAL_SECONDS",
        str(_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS),
    ).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            "CAREEROPS_HH_REQUEST_MIN_INTERVAL_SECONDS must be a number"
        ) from exc
    if value < 0:
        raise ValueError(
            "CAREEROPS_HH_REQUEST_MIN_INTERVAL_SECONDS must be >= 0"
        )
    return value


@dataclass(frozen=True, slots=True)
class HHSearchPageRequest:
    """Parameters for exactly one HH vacancy search page."""

    text: str
    page: int
    area: int = 1
    period: int = 14
    order_by: str = "publication_time"
    per_page: int = 50
    professional_roles: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("search text must not be empty")
        if self.page < 0:
            raise ValueError("page must be >= 0")
        if not 1 <= self.per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")
        if self.period < 1:
            raise ValueError("period must be >= 1")

    def api_params(self) -> dict[str, ParamValue]:
        """Return source parameters without adding CareerOPS task metadata."""

        params: dict[str, ParamValue] = {
            "text": self.text,
            "area": self.area,
            "period": self.period,
            "order_by": self.order_by,
            "per_page": self.per_page,
            "page": self.page,
        }
        if self.professional_roles:
            params["professional_role"] = self.professional_roles
        return params


@dataclass(frozen=True, slots=True)
class HHResumeListPageRequest:
    """Parameters for exactly one authoritative /resumes/mine page."""

    page: int = 0
    per_page: int = 100

    def __post_init__(self) -> None:
        if self.page < 0:
            raise ValueError("page must be >= 0")
        if not 1 <= self.per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")

    def api_params(self) -> dict[str, ParamValue]:
        """Return exact source parameters for the requested inventory page."""

        return {"page": self.page, "per_page": self.per_page}


class HHReadTransport(Protocol):
    """Read operations needed by HH ingestion before any application workflow."""

    async def search_page(self, request: HHSearchPageRequest) -> dict[str, Any]:
        """Return one exact HH vacancy-search page."""

        ...

    async def fetch_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        """Return one exact full vacancy response."""

        ...

    async def list_resume_page(
        self,
        request: HHResumeListPageRequest,
    ) -> dict[str, Any]:
        """Return one exact /resumes/mine page."""

        ...

    async def fetch_resume(self, resume_id: str) -> dict[str, Any]:
        """Return one exact full resume response."""

        ...


def _legacy_failure_kind(message: str) -> HHFailureKind:
    """Classify only source signals that the current CLI exposes reliably enough."""

    normalized = message.casefold()
    if "captcha_required" in normalized or "captcha" in normalized:
        return HHFailureKind.CAPTCHA_REQUIRED
    if "429" in normalized or "too many requests" in normalized:
        return HHFailureKind.RATE_LIMITED
    if "401" in normalized or "unauthorized" in normalized:
        return HHFailureKind.AUTH_REQUIRED
    if "token" in normalized and "expired" in normalized:
        return HHFailureKind.SESSION_EXPIRED
    return HHFailureKind.UNKNOWN_RESPONSE


class HHApplicantToolTransport:
    """Async CareerOPS boundary around the existing pinned HH CLI driver.

    The vendor hh-applicant-tool remains the actual HH transport implementation.
    CareerOPS only serializes calls and enforces a conservative minimum request
    start interval around it so one account cannot hammer HH accidentally.
    """

    def __init__(
        self,
        driver: HHApplicantToolCLI,
        *,
        min_request_interval_seconds: float | None = None,
    ) -> None:
        self._driver = driver
        interval = (
            _request_min_interval_from_env()
            if min_request_interval_seconds is None
            else min_request_interval_seconds
        )
        if interval < 0:
            raise ValueError("min_request_interval_seconds must be >= 0")
        self._min_request_interval_seconds = float(interval)
        self._request_lock = asyncio.Lock()
        self._last_request_started_at: float | None = None

    async def _wait_for_request_slot(self) -> None:
        if self._last_request_started_at is None:
            return
        elapsed = time.monotonic() - self._last_request_started_at
        remaining = self._min_request_interval_seconds - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)

    async def _call_api(
        self,
        endpoint: str,
        *,
        params: dict[str, ParamValue] | None = None,
        operation: str,
    ) -> dict[str, Any]:
        async with self._request_lock:
            await self._wait_for_request_slot()
            self._last_request_started_at = time.monotonic()
            try:
                return await asyncio.to_thread(
                    self._driver.call_api,
                    endpoint,
                    params=params,
                )
            except subprocess.TimeoutExpired as exc:
                raise HHTransportError(
                    kind=HHFailureKind.TEMPORARY_HTTP_ERROR,
                    operation=operation,
                    message="upstream CLI timed out",
                ) from exc
            except HHDriverError as exc:
                message = str(exc)
                raise HHTransportError(
                    kind=_legacy_failure_kind(message),
                    operation=operation,
                    message=message,
                ) from exc

    async def search_page(self, request: HHSearchPageRequest) -> dict[str, Any]:
        """Fetch one page without flattening or deduplicating source items."""

        return await self._call_api(
            "vacancies",
            params=request.api_params(),
            operation=f"search_page[{request.page}]",
        )

    async def fetch_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        """Fetch one full vacancy through the existing read-only transport."""

        normalized_id = vacancy_id.strip()
        if not normalized_id:
            raise ValueError("vacancy_id must not be empty")
        return await self._call_api(
            f"vacancies/{normalized_id}",
            operation=f"fetch_vacancy[{normalized_id}]",
        )

    async def list_resume_page(
        self,
        request: HHResumeListPageRequest,
    ) -> dict[str, Any]:
        """Fetch one inventory page so RAW keeps the actual upstream envelope."""

        return await self._call_api(
            "resumes/mine",
            params=request.api_params(),
            operation=f"list_resume_page[{request.page}]",
        )

    async def fetch_resume(self, resume_id: str) -> dict[str, Any]:
        """Fetch one full resume without applying CareerOPS binding policy."""

        normalized_id = resume_id.strip()
        if not normalized_id:
            raise ValueError("resume_id must not be empty")
        return await self._call_api(
            f"resumes/{normalized_id}",
            operation=f"fetch_resume[{normalized_id}]",
        )
