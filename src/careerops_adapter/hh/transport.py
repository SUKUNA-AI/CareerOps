"""Read-only transport boundary HH source adapter v2

Начальная реализация оборачивает pinned hh-applicant-tool CLI и не дублирует его
authentication и protocol behavior
Здесь нет filtering, scoring, scheduling или application policy
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
    """Параметры одного page запроса поиска вакансий HH"""

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
        """Возвращает source parameters без служебной metadata CareerOPS"""

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
    """Параметры одного authoritative page запроса /resumes/mine"""

    page: int = 0
    per_page: int = 100

    def __post_init__(self) -> None:
        if self.page < 0:
            raise ValueError("page must be >= 0")
        if not 1 <= self.per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")

    def api_params(self) -> dict[str, ParamValue]:
        """Возвращает точные source parameters запрошенного inventory page"""

        return {"page": self.page, "per_page": self.per_page}


class HHReadTransport(Protocol):
    """Read-only операции, необходимые HH ingestion"""

    async def search_page(self, request: HHSearchPageRequest) -> dict[str, Any]:
        """Возвращает один точный page поисковой выдачи HH"""

        ...

    async def fetch_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        """Возвращает один точный full vacancy response"""

        ...

    async def list_resume_page(
        self,
        request: HHResumeListPageRequest,
    ) -> dict[str, Any]:
        """Возвращает один точный page /resumes/mine"""

        ...

    async def fetch_resume(self, resume_id: str) -> dict[str, Any]:
        """Возвращает один точный full resume response"""

        ...


def _classify_cli_failure(message: str) -> HHFailureKind:
    """Классифицирует только сигналы, которые надёжно доступны через текущий CLI"""

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
    """Async boundary CareerOPS поверх pinned HH CLI driver

    Vendored hh-applicant-tool остаётся фактической реализацией HH transport
    CareerOPS сериализует вызовы и выдерживает минимальный интервал между стартами
    запросов одного account, чтобы случайно не перегружать HH
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
                    kind=_classify_cli_failure(message),
                    operation=operation,
                    message=message,
                ) from exc

    async def search_page(self, request: HHSearchPageRequest) -> dict[str, Any]:
        """Получает один page без flatten или dedup source items"""

        return await self._call_api(
            "vacancies",
            params=request.api_params(),
            operation=f"search_page[{request.page}]",
        )

    async def fetch_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        """Получает одну full vacancy через read-only transport"""

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
        """Получает один inventory page без изменения upstream envelope"""

        return await self._call_api(
            "resumes/mine",
            params=request.api_params(),
            operation=f"list_resume_page[{request.page}]",
        )

    async def fetch_resume(self, resume_id: str) -> dict[str, Any]:
        """Получает одно full resume без применения binding policy CareerOPS"""

        normalized_id = resume_id.strip()
        if not normalized_id:
            raise ValueError("resume_id must not be empty")
        return await self._call_api(
            f"resumes/{normalized_id}",
            operation=f"fetch_resume[{normalized_id}]",
        )
