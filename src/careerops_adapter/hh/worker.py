"""Execute claimed HH source tasks without filtering or domain materialization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from .errors import (
    HHFailureDisposition,
    HHFailureKind,
    HHTransportError,
    default_failure_disposition,
)
from .raw import (
    HHRawContext,
    HHRawPublisher,
    RawObjectCollisionError,
    RawWriteVerificationError,
)
from .tasks import (
    SourceTaskKind,
    SourceTaskRecord,
    SourceTaskRepository,
    SourceTaskSpec,
    resume_fetch_task,
    resume_sync_task,
    search_page_task,
    vacancy_fetch_task,
)
from .transport import HHReadTransport, HHResumeListPageRequest, HHSearchPageRequest

Clock = Callable[[], datetime]
UuidFactory = Callable[[], UUID]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class HHSourceFailurePolicy:
    """Small transport retry policy; daily orchestration remains outside the adapter."""

    retry_delay: timedelta = timedelta(minutes=5)
    defer_delay: timedelta = timedelta(minutes=30)
    account_block_delay: timedelta = timedelta(hours=6)

    def next_attempt(
        self,
        *,
        disposition: HHFailureDisposition,
        now: datetime,
    ) -> datetime:
        if disposition is HHFailureDisposition.RETRY:
            return now + self.retry_delay
        if disposition is HHFailureDisposition.BLOCK_ACCOUNT:
            return now + self.account_block_delay
        return now + self.defer_delay


class SourceTaskRunOutcome(StrEnum):
    """Observable result of one claimed task execution."""

    SUCCEEDED = "succeeded"
    DEFERRED = "deferred"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"


@dataclass(frozen=True, slots=True)
class SourceTaskRunResult:
    """Compact worker result; RAW details stay in S3 and source_tasks."""

    task_id: UUID
    outcome: SourceTaskRunOutcome
    raw_uri: str | None = None
    child_count: int = 0
    account_blocked: bool = False
    error_kind: HHFailureKind | None = None


def _required_str(parameters: dict[str, Any], key: str) -> str:
    value = parameters.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HHTransportError(
            kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
            operation="source_task_parameters",
            message=f"missing or invalid {key}",
        )
    return value.strip()


def _required_int(parameters: dict[str, Any], key: str) -> int:
    value = parameters.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise HHTransportError(
            kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
            operation="source_task_parameters",
            message=f"missing or invalid {key}",
        )
    return value


def _generation_id(parameters: dict[str, Any]) -> UUID:
    raw = _required_str(parameters, "generation_id")
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HHTransportError(
            kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
            operation="source_task_parameters",
            message="generation_id is not a UUID",
        ) from exc


def _items(payload: dict[str, Any], operation: str) -> list[dict[str, Any]]:
    value = payload.get("items")
    if not isinstance(value, list):
        raise HHTransportError(
            kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
            operation=operation,
            message="source response items is not a list",
        )
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise HHTransportError(
                kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
                operation=operation,
                message="source response item is not an object",
            )
        items.append(item)
    return items


def _pages(payload: dict[str, Any], operation: str) -> int:
    value = payload.get("pages")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HHTransportError(
            kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
            operation=operation,
            message="source response pages is not a non-negative integer",
        )
    return value


class HHSourceTaskExecutor:
    """Run one claimed HH task: source call -> immutable RAW -> persistent children."""

    def __init__(
        self,
        *,
        transport: HHReadTransport,
        raw: HHRawPublisher,
        repository: SourceTaskRepository,
        failure_policy: HHSourceFailurePolicy | None = None,
        clock: Clock = _utc_now,
        uuid_factory: UuidFactory = uuid4,
    ) -> None:
        self._transport = transport
        self._raw = raw
        self._repository = repository
        self._failure_policy = failure_policy or HHSourceFailurePolicy()
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def run(self, task: SourceTaskRecord) -> SourceTaskRunResult:
        """Execute one claimed task and persist its terminal/current queue state."""

        await self._repository.mark_running(task)
        try:
            raw_uri, children = await self._execute(task)
        except HHTransportError as exc:
            return await self._handle_transport_error(task, exc)
        except RawObjectCollisionError:
            await self._repository.terminal_failure(
                task,
                error_category="raw_collision",
            )
            return SourceTaskRunResult(
                task_id=task.id,
                outcome=SourceTaskRunOutcome.TERMINAL_FAILURE,
            )
        except (RawWriteVerificationError, ClientError):
            now = self._now()
            await self._repository.retryable_failure(
                task,
                error_category="raw_storage",
                next_attempt_at=now + self._failure_policy.retry_delay,
            )
            return SourceTaskRunResult(
                task_id=task.id,
                outcome=SourceTaskRunOutcome.RETRYABLE_FAILURE,
            )

        await self._repository.succeed_with_children(
            task,
            result_artifact_uri=raw_uri,
            children=children,
        )
        return SourceTaskRunResult(
            task_id=task.id,
            outcome=SourceTaskRunOutcome.SUCCEEDED,
            raw_uri=raw_uri,
            child_count=len(children),
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("HH source worker clock must be timezone-aware")
        return value.astimezone(UTC)

    def _raw_context(self, task: SourceTaskRecord, profile_key: str) -> HHRawContext:
        return HHRawContext(
            account_key=task.account_key,
            profile_key=profile_key,
            observed_at=self._now(),
            observation_id=self._uuid_factory(),
        )

    async def _execute(
        self,
        task: SourceTaskRecord,
    ) -> tuple[str, tuple[SourceTaskSpec, ...]]:
        match task.kind:
            case SourceTaskKind.SEARCH_PAGE:
                return await self._search_page(task)
            case SourceTaskKind.VACANCY_FETCH:
                return await self._vacancy_fetch(task)
            case SourceTaskKind.RESUME_SYNC:
                return await self._resume_sync(task)
            case SourceTaskKind.RESUME_FETCH:
                return await self._resume_fetch(task)
            case SourceTaskKind.SEARCH:
                raise HHTransportError(
                    kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
                    operation="source_task",
                    message="search is an orchestration/root task and is not worker-executable",
                )
        raise HHTransportError(
            kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
            operation="source_task",
            message=f"unsupported task kind {task.kind}",
        )

    async def _search_page(
        self,
        task: SourceTaskRecord,
    ) -> tuple[str, tuple[SourceTaskSpec, ...]]:
        p = task.parameters
        generation_id = _generation_id(p)
        profile_key = _required_str(p, "profile_key")
        query_key = _required_str(p, "query_key")
        page = _required_int(p, "page")
        max_pages = _required_int(p, "max_pages")
        roles = p.get("professional_roles", [])
        if not isinstance(roles, list) or any(
            isinstance(role, bool) or not isinstance(role, int) for role in roles
        ):
            raise HHTransportError(
                kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
                operation="source_task_parameters",
                message="professional_roles must be a list of integers",
            )
        request = HHSearchPageRequest(
            text=_required_str(p, "text"),
            page=page,
            area=_required_int(p, "area"),
            period=_required_int(p, "period"),
            order_by=_required_str(p, "order_by"),
            per_page=_required_int(p, "per_page"),
            professional_roles=tuple(roles),
        )
        payload = await self._transport.search_page(request)
        raw = await self._raw.publish_search_page(
            context=self._raw_context(task, profile_key),
            query_key=query_key,
            page=page,
            payload=payload,
        )

        children: list[SourceTaskSpec] = []
        seen_vacancies: set[str] = set()
        for item in _items(payload, "search_page"):
            vacancy_id = str(item.get("id") or "").strip()
            if not vacancy_id or vacancy_id in seen_vacancies:
                continue
            seen_vacancies.add(vacancy_id)
            children.append(
                vacancy_fetch_task(
                    generation_id=generation_id,
                    profile_key=profile_key,
                    vacancy_id=vacancy_id,
                    parent_task_id=task.id,
                )
            )

        total_pages = _pages(payload, "search_page")
        next_page = page + 1
        if next_page < total_pages and next_page < max_pages:
            children.append(
                search_page_task(
                    generation_id=generation_id,
                    profile_key=profile_key,
                    query_key=query_key,
                    text=request.text,
                    page=next_page,
                    area=request.area,
                    period=request.period,
                    order_by=request.order_by,
                    per_page=request.per_page,
                    professional_roles=request.professional_roles,
                    max_pages=max_pages,
                    parent_task_id=task.id,
                )
            )
        return raw.ref.uri, tuple(children)

    async def _vacancy_fetch(
        self,
        task: SourceTaskRecord,
    ) -> tuple[str, tuple[SourceTaskSpec, ...]]:
        p = task.parameters
        profile_key = _required_str(p, "profile_key")
        vacancy_id = _required_str(p, "vacancy_id")
        payload = await self._transport.fetch_vacancy(vacancy_id)
        raw = await self._raw.publish_vacancy(
            context=self._raw_context(task, profile_key),
            vacancy_id=vacancy_id,
            payload=payload,
        )
        return raw.ref.uri, ()

    async def _resume_sync(
        self,
        task: SourceTaskRecord,
    ) -> tuple[str, tuple[SourceTaskSpec, ...]]:
        p = task.parameters
        generation_id = _generation_id(p)
        profile_key = _required_str(p, "profile_key")
        page = _required_int(p, "page")
        per_page = _required_int(p, "per_page")
        payload = await self._transport.list_resume_page(
            HHResumeListPageRequest(page=page, per_page=per_page)
        )
        raw = await self._raw.publish_resume_list_page(
            context=self._raw_context(task, profile_key),
            page=page,
            payload=payload,
        )

        children: list[SourceTaskSpec] = []
        seen_resumes: set[str] = set()
        for item in _items(payload, "resume_sync"):
            resume_id = str(item.get("id") or "").strip()
            if not resume_id or resume_id in seen_resumes:
                continue
            seen_resumes.add(resume_id)
            children.append(
                resume_fetch_task(
                    generation_id=generation_id,
                    profile_key=profile_key,
                    resume_id=resume_id,
                    parent_task_id=task.id,
                )
            )

        total_pages = _pages(payload, "resume_sync")
        if page + 1 < total_pages:
            children.append(
                resume_sync_task(
                    generation_id=generation_id,
                    profile_key=profile_key,
                    page=page + 1,
                    parent_task_id=task.id,
                )
            )
        return raw.ref.uri, tuple(children)

    async def _resume_fetch(
        self,
        task: SourceTaskRecord,
    ) -> tuple[str, tuple[SourceTaskSpec, ...]]:
        p = task.parameters
        profile_key = _required_str(p, "profile_key")
        resume_id = _required_str(p, "resume_id")
        payload = await self._transport.fetch_resume(resume_id)
        raw = await self._raw.publish_resume(
            context=self._raw_context(task, profile_key),
            resume_id=resume_id,
            payload=payload,
        )
        return raw.ref.uri, ()

    async def _handle_transport_error(
        self,
        task: SourceTaskRecord,
        exc: HHTransportError,
    ) -> SourceTaskRunResult:
        disposition = default_failure_disposition(exc.kind)
        if disposition is HHFailureDisposition.TERMINAL:
            await self._repository.terminal_failure(
                task,
                error_category=exc.kind.value,
            )
            return SourceTaskRunResult(
                task_id=task.id,
                outcome=SourceTaskRunOutcome.TERMINAL_FAILURE,
                error_kind=exc.kind,
            )

        now = self._now()
        next_attempt = self._failure_policy.next_attempt(
            disposition=disposition,
            now=now,
        )
        if disposition is HHFailureDisposition.RETRY:
            await self._repository.retryable_failure(
                task,
                error_category=exc.kind.value,
                next_attempt_at=next_attempt,
            )
            return SourceTaskRunResult(
                task_id=task.id,
                outcome=SourceTaskRunOutcome.RETRYABLE_FAILURE,
                error_kind=exc.kind,
            )

        await self._repository.defer(
            task,
            error_category=exc.kind.value,
            next_attempt_at=next_attempt,
        )
        return SourceTaskRunResult(
            task_id=task.id,
            outcome=SourceTaskRunOutcome.DEFERRED,
            account_blocked=disposition is HHFailureDisposition.BLOCK_ACCOUNT,
            error_kind=exc.kind,
        )
