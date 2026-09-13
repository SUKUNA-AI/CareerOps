"""Выполнение claimed HH source tasks без filtering и domain materialization"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

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
from .watermarks import HHSearchWatermarkStore

Clock = Callable[[], datetime]
UuidFactory = Callable[[], UUID]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class HHSourceFailurePolicy:
    """Retry policy transport-уровня без daily orchestration"""

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
    """Наблюдаемый результат выполнения одной claimed task"""

    SUCCEEDED = "succeeded"
    DEFERRED = "deferred"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"


@dataclass(frozen=True, slots=True)
class SourceTaskRunResult:
    """Краткий результат worker, RAW детали остаются в S3 и source_tasks"""

    task_id: UUID
    outcome: SourceTaskRunOutcome
    raw_uri: str | None = None
    child_count: int = 0
    account_blocked: bool = False
    error_kind: HHFailureKind | None = None


@dataclass(frozen=True, slots=True)
class _SearchWatermarkAdvance:
    profile_key: str
    query_key: str
    generation_id: UUID
    published_at: datetime
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class _ExecutionResult:
    raw_uri: str
    children: tuple[SourceTaskSpec, ...]
    watermark: _SearchWatermarkAdvance | None = None


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


def _optional_datetime(parameters: dict[str, Any], key: str) -> datetime | None:
    value = parameters.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise HHTransportError(
            kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
            operation="source_task_parameters",
            message=f"invalid {key}",
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HHTransportError(
            kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
            operation="source_task_parameters",
            message=f"invalid {key}",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HHTransportError(
            kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
            operation="source_task_parameters",
            message=f"timezone-naive {key}",
        )
    return parsed.astimezone(UTC)


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


def _item_published_at(item: dict[str, Any]) -> datetime | None:
    value = item.get("published_at")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _max_datetime(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _page_watermark_state(
    items: list[dict[str, Any]],
    *,
    previous_watermark: datetime | None,
    overlap_seconds: int,
) -> tuple[datetime | None, bool]:
    """Возвращает новый page timestamp и факт достижения безопасной overlap boundary

    Page с отсутствующим или неразбираемым `published_at` не доказывает достижение boundary
    В таком случае pagination продолжается до исчерпания source, чтобы не получить false stop
    """

    timestamps = [_item_published_at(item) for item in items]
    valid = [value for value in timestamps if value is not None]
    newest = max(valid) if valid else None
    if previous_watermark is None or not items or len(valid) != len(items):
        return newest, False
    boundary = previous_watermark - timedelta(seconds=overlap_seconds)
    return newest, min(valid) <= boundary


def _search_continuation(
    *,
    task: SourceTaskRecord,
    request: HHSearchPageRequest,
    generation_id: UUID,
    profile_key: str,
    query_key: str,
    next_page: int,
    max_pages: int,
    previous_watermark: datetime | None,
    candidate_watermark: datetime | None,
    overlap_seconds: int,
) -> SourceTaskSpec:
    base = search_page_task(
        generation_id=generation_id,
        profile_key=profile_key,
        query_key=query_key,
        text=request.text,
        page=next_page,
        max_pages=max_pages,
        area=request.area,
        period=request.period,
        order_by=request.order_by,
        per_page=request.per_page,
        professional_roles=request.professional_roles,
        parent_task_id=task.id,
    )
    parameters = dict(base.parameters)
    parameters["previous_watermark_at"] = (
        previous_watermark.isoformat() if previous_watermark is not None else None
    )
    parameters["candidate_watermark_at"] = (
        candidate_watermark.isoformat() if candidate_watermark is not None else None
    )
    parameters["watermark_overlap_seconds"] = overlap_seconds
    return SourceTaskSpec.build(
        SourceTaskKind.SEARCH_PAGE,
        parameters,
        parent_task_id=task.id,
    )


class HHSourceTaskExecutor:
    """Выполняет claimed HH task: source call → immutable RAW → persistent children"""

    def __init__(
        self,
        *,
        transport: HHReadTransport,
        raw: HHRawPublisher,
        repository: SourceTaskRepository,
        watermarks: HHSearchWatermarkStore,
        failure_policy: HHSourceFailurePolicy | None = None,
        clock: Clock = _utc_now,
        uuid_factory: UuidFactory = uuid4,
    ) -> None:
        self._transport = transport
        self._raw = raw
        self._repository = repository
        self._watermarks = watermarks
        self._failure_policy = failure_policy or HHSourceFailurePolicy()
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def run(self, task: SourceTaskRecord) -> SourceTaskRunResult:
        """Выполняет claimed task и сохраняет её актуальный queue state"""

        await self._repository.mark_running(task)
        try:
            execution = await self._execute(task)
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
        except (RawWriteVerificationError, ClientError, BotoCoreError):
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
            result_artifact_uri=execution.raw_uri,
            children=execution.children,
        )
        # Watermark двигается только после успешного task и durable создания children
        # Ошибка здесь приводит только к повторному чтению в следующей generation
        # Она не может заставить source cursor пропустить несохранённую работу
        if execution.watermark is not None:
            watermark = execution.watermark
            await self._watermarks.advance(
                account_id=task.account_id,
                profile_key=watermark.profile_key,
                query_key=watermark.query_key,
                published_at=watermark.published_at,
                generation_id=watermark.generation_id,
                observed_at=watermark.observed_at,
            )
        return SourceTaskRunResult(
            task_id=task.id,
            outcome=SourceTaskRunOutcome.SUCCEEDED,
            raw_uri=execution.raw_uri,
            child_count=len(execution.children),
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

    async def _execute(self, task: SourceTaskRecord) -> _ExecutionResult:
        match task.kind:
            case SourceTaskKind.SEARCH_PAGE:
                return await self._search_page(task)
            case SourceTaskKind.VACANCY_FETCH:
                return await self._vacancy_fetch(task)
            case SourceTaskKind.RESUME_SYNC:
                return await self._resume_sync(task)
            case SourceTaskKind.RESUME_FETCH:
                return await self._resume_fetch(task)
        raise HHTransportError(
            kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
            operation="source_task",
            message=f"unsupported task kind {task.kind}",
        )

    async def _search_page(self, task: SourceTaskRecord) -> _ExecutionResult:
        p = task.parameters
        generation_id = _generation_id(p)
        profile_key = _required_str(p, "profile_key")
        query_key = _required_str(p, "query_key")
        page = _required_int(p, "page")
        max_pages = _required_int(p, "max_pages")
        overlap_seconds = _required_int(p, "watermark_overlap_seconds")
        if overlap_seconds < 0:
            raise HHTransportError(
                kind=HHFailureKind.PERMANENT_SOURCE_ERROR,
                operation="source_task_parameters",
                message="watermark_overlap_seconds must be >= 0",
            )
        previous_watermark = _optional_datetime(p, "previous_watermark_at")
        candidate_watermark = _optional_datetime(p, "candidate_watermark_at")
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
        raw_context = self._raw_context(task, profile_key)
        raw = await self._raw.publish_search_page(
            context=raw_context,
            query_key=query_key,
            page=page,
            payload=payload,
        )

        source_items = _items(payload, "search_page")
        children: list[SourceTaskSpec] = []
        seen_vacancies: set[str] = set()
        for item in source_items:
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

        newest_on_page, watermark_reached = _page_watermark_state(
            source_items,
            previous_watermark=previous_watermark,
            overlap_seconds=overlap_seconds,
        )
        candidate_watermark = _max_datetime(candidate_watermark, newest_on_page)
        total_pages = _pages(payload, "search_page")
        next_page = page + 1
        has_next_page = next_page < total_pages and next_page < max_pages
        if has_next_page and not watermark_reached:
            children.append(
                _search_continuation(
                    task=task,
                    request=request,
                    generation_id=generation_id,
                    profile_key=profile_key,
                    query_key=query_key,
                    next_page=next_page,
                    max_pages=max_pages,
                    previous_watermark=previous_watermark,
                    candidate_watermark=candidate_watermark,
                    overlap_seconds=overlap_seconds,
                )
            )
            watermark_advance = None
        elif candidate_watermark is not None:
            watermark_advance = _SearchWatermarkAdvance(
                profile_key=profile_key,
                query_key=query_key,
                generation_id=generation_id,
                published_at=candidate_watermark,
                observed_at=raw_context.observed_at,
            )
        else:
            watermark_advance = None

        return _ExecutionResult(
            raw_uri=raw.ref.uri,
            children=tuple(children),
            watermark=watermark_advance,
        )

    async def _vacancy_fetch(self, task: SourceTaskRecord) -> _ExecutionResult:
        p = task.parameters
        profile_key = _required_str(p, "profile_key")
        vacancy_id = _required_str(p, "vacancy_id")
        payload = await self._transport.fetch_vacancy(vacancy_id)
        raw = await self._raw.publish_vacancy(
            context=self._raw_context(task, profile_key),
            vacancy_id=vacancy_id,
            payload=payload,
        )
        return _ExecutionResult(raw_uri=raw.ref.uri, children=())

    async def _resume_sync(self, task: SourceTaskRecord) -> _ExecutionResult:
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
        return _ExecutionResult(raw_uri=raw.ref.uri, children=tuple(children))

    async def _resume_fetch(self, task: SourceTaskRecord) -> _ExecutionResult:
        p = task.parameters
        profile_key = _required_str(p, "profile_key")
        resume_id = _required_str(p, "resume_id")
        payload = await self._transport.fetch_resume(resume_id)
        raw = await self._raw.publish_resume(
            context=self._raw_context(task, profile_key),
            resume_id=resume_id,
            payload=payload,
        )
        return _ExecutionResult(raw_uri=raw.ref.uri, children=())

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
