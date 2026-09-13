"""Формирование корневой HH source generation без scheduling policy

Generation определяется внешним UUID
Повторный запуск уже созданной generation использует существующие root tasks и не
перечитывает текущие watermark или конфигурацию, поэтому retry orchestration не может
изменить границу сканирования посреди generation

Все выбранные запросы превращаются в persistent SEARCH_PAGE tasks
Пропускная способность ограничивается позже worker-ом
Синхронизация списка резюме создаётся отдельной root task только когда она явно запрошена
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from careerops_integrations.hh.configuration import (
    DiscoveryConfig,
    HHAccountConfig,
    HHConfigError,
    accounts_config_path_from_env,
    discovery_config_path_from_env,
    load_accounts_config,
    load_discovery_config,
)

from .postgres import HHPostgresSettings, resolve_hh_account_id
from .tasks import (
    SourceTaskKind,
    SourceTaskRepository,
    SourceTaskSpec,
    resume_sync_task,
    search_page_task,
)
from .watermarks import HHSearchWatermarkStore

# Только защитный предел от повреждённой pagination
# Штатная остановка определяется source-reported pages, watermark или исчерпанием source
_SOURCE_PAGE_SAFETY_CAP = 10_000
_DEFAULT_WATERMARK_OVERLAP_SECONDS = 3600


class SourceSeedKind(StrEnum):
    """Наборы root work, которые orchestration может создать для одной generation"""

    SEARCH = "search"
    RESUMES = "resumes"
    ALL = "all"

    @property
    def includes_search(self) -> bool:
        return self in {SourceSeedKind.SEARCH, SourceSeedKind.ALL}

    @property
    def includes_resumes(self) -> bool:
        return self in {SourceSeedKind.RESUMES, SourceSeedKind.ALL}


@dataclass(frozen=True, slots=True)
class HHSourceGenerationPlan:
    """План root tasks до записи в PostgreSQL"""

    generation_id: UUID
    account_key: str
    profile_key: str
    search_tasks: tuple[SourceTaskSpec, ...]
    resume_tasks: tuple[SourceTaskSpec, ...]

    @property
    def tasks(self) -> tuple[SourceTaskSpec, ...]:
        return self.search_tasks + self.resume_tasks


@dataclass(frozen=True, slots=True)
class HHSourceSeedSummary:
    """Краткий результат атомарного создания root generation в PostgreSQL"""

    generation_id: str
    account_key: str
    profile_key: str
    kind: str
    search_tasks: int
    resume_tasks: int
    ensured_tasks: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _query_value(value: int | None, default: int) -> int:
    return default if value is None else value


def _watermark_overlap_seconds_from_env() -> int:
    raw = os.getenv(
        "CAREEROPS_HH_WATERMARK_OVERLAP_SECONDS",
        str(_DEFAULT_WATERMARK_OVERLAP_SECONDS),
    ).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("CAREEROPS_HH_WATERMARK_OVERLAP_SECONDS must be an integer") from exc
    if value < 0:
        raise ValueError("CAREEROPS_HH_WATERMARK_OVERLAP_SECONDS must be >= 0")
    return value


def _watermark_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("search watermark must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _watermarked_search_root(
    *,
    base: SourceTaskSpec,
    previous_watermark: datetime | None,
    overlap_seconds: int,
) -> SourceTaskSpec:
    parameters = dict(base.parameters)
    parameters["previous_watermark_at"] = _watermark_iso(previous_watermark)
    parameters["candidate_watermark_at"] = None
    parameters["watermark_overlap_seconds"] = overlap_seconds
    return SourceTaskSpec.build(SourceTaskKind.SEARCH_PAGE, parameters)


def build_source_generation_plan(
    *,
    account: HHAccountConfig,
    discovery: DiscoveryConfig | None,
    generation_id: UUID,
    kind: SourceSeedKind,
    watermarks: Mapping[str, datetime] | None = None,
    watermark_overlap_seconds: int = _DEFAULT_WATERMARK_OVERLAP_SECONDS,
) -> HHSourceGenerationPlan:
    """Строит все стартовые persistent tasks для одной observation generation

    Каждый выбранный query начинается с page=0
    Последующие страницы создаются по source-reported page count и останавливаются по
    предыдущему publication watermark с overlap либо по исчерпанию source
    Safety cap нужен только как защита от повреждённой pagination
    """

    if generation_id.int == 0:
        raise ValueError("generation_id must not be the nil UUID")
    if watermark_overlap_seconds < 0:
        raise ValueError("watermark_overlap_seconds must be >= 0")

    known_watermarks = watermarks or {}
    search_tasks: list[SourceTaskSpec] = []
    if kind.includes_search:
        if discovery is None:
            raise ValueError("search generation requires discovery configuration")
        query_set_keys = account.query_set_keys
        if not query_set_keys:
            raise HHConfigError(
                f"account {account.key!r} has no enabled query-set bindings"
            )
        selected = discovery.select_queries(query_set_keys)
        if not selected:
            raise HHConfigError(
                f"account {account.key!r} resolves to no enabled discovery queries"
            )

        defaults = discovery.defaults
        for query in selected:
            spec = query.spec
            base = search_page_task(
                generation_id=generation_id,
                profile_key=account.profile,
                query_key=spec.key,
                text=spec.text,
                page=0,
                max_pages=_SOURCE_PAGE_SAFETY_CAP,
                area=_query_value(spec.area, defaults.area),
                period=_query_value(spec.period, defaults.period),
                order_by=defaults.order_by,
                per_page=_query_value(spec.per_page, defaults.per_page),
            )
            search_tasks.append(
                _watermarked_search_root(
                    base=base,
                    previous_watermark=known_watermarks.get(spec.key),
                    overlap_seconds=watermark_overlap_seconds,
                )
            )

    resume_tasks: tuple[SourceTaskSpec, ...] = ()
    if kind.includes_resumes:
        resume_tasks = (
            resume_sync_task(
                generation_id=generation_id,
                profile_key=account.profile,
                page=0,
            ),
        )

    return HHSourceGenerationPlan(
        generation_id=generation_id,
        account_key=account.key,
        profile_key=account.profile,
        search_tasks=tuple(search_tasks),
        resume_tasks=resume_tasks,
    )


async def _existing_generation_counts(
    conn: Any,
    *,
    account_id: int,
    generation_id: UUID,
) -> tuple[int, int]:
    cursor = await conn.execute(
        """
        SELECT task_kind, count(*)
        FROM careerops_v2.source_tasks
        WHERE account_id = %s
          AND parent_task_id IS NULL
          AND parameters ->> 'generation_id' = %s
          AND task_kind IN ('search_page', 'resume_sync')
        GROUP BY task_kind
        """,
        (account_id, str(generation_id)),
    )
    search_count = 0
    resume_count = 0
    for task_kind, count in await cursor.fetchall():
        if task_kind == SourceTaskKind.SEARCH_PAGE.value:
            search_count = int(count)
        elif task_kind == SourceTaskKind.RESUME_SYNC.value:
            resume_count = int(count)
    return search_count, resume_count


def _kind_from_counts(search_count: int, resume_count: int) -> SourceSeedKind | None:
    if search_count and resume_count:
        return SourceSeedKind.ALL
    if search_count:
        return SourceSeedKind.SEARCH
    if resume_count:
        return SourceSeedKind.RESUMES
    return None


async def seed_source_generation(
    *,
    account_key: str,
    generation_id: UUID,
    kind: SourceSeedKind,
    accounts_config: Path | None = None,
    discovery_config: Path | None = None,
) -> HHSourceSeedSummary:
    """Атомарно создаёт стартовые tasks для явно заданной generation

    Функция читает только локальную конфигурацию и пишет control state в PostgreSQL v2
    Вызовов HH или S3 здесь нет
    Существующая generation immutable, поэтому retry с тем же UUID возвращает уже
    созданный root set
    """

    accounts_path = accounts_config or accounts_config_path_from_env()
    discovery: DiscoveryConfig | None = None
    if kind.includes_search:
        discovery_path = discovery_config or discovery_config_path_from_env()
        discovery = load_discovery_config(discovery_path)
        accounts = load_accounts_config(accounts_path, discovery=discovery)
    else:
        accounts = load_accounts_config(accounts_path)

    account = accounts.resolve_account(account_key.strip())
    settings = HHPostgresSettings.from_env()
    conn = await psycopg.AsyncConnection.connect(settings.dsn, autocommit=True)
    try:
        account_id = await resolve_hh_account_id(
            conn,
            account_key=account.key,
            profile_key=account.profile,
        )
        existing_search, existing_resume = await _existing_generation_counts(
            conn,
            account_id=account_id,
            generation_id=generation_id,
        )
        existing_kind = _kind_from_counts(existing_search, existing_resume)
        if existing_kind is not None:
            if existing_kind is not kind:
                raise ValueError(
                    f"generation {generation_id} already exists as {existing_kind.value!r}, "
                    f"not {kind.value!r}"
                )
            return HHSourceSeedSummary(
                generation_id=str(generation_id),
                account_key=account.key,
                profile_key=account.profile,
                kind=kind.value,
                search_tasks=existing_search,
                resume_tasks=existing_resume,
                ensured_tasks=existing_search + existing_resume,
            )

        watermarks: dict[str, datetime] = {}
        if kind.includes_search:
            watermarks = await HHSearchWatermarkStore(conn).load_for_profile(
                account_id=account_id,
                profile_key=account.profile,
            )
        plan = build_source_generation_plan(
            account=account,
            discovery=discovery,
            generation_id=generation_id,
            kind=kind,
            watermarks=watermarks,
            watermark_overlap_seconds=_watermark_overlap_seconds_from_env(),
        )
        repository = SourceTaskRepository(conn)
        # Root seeding выполняется целиком или не выполняется вообще
        # При retry ON CONFLICT возвращает существующие task id и не сбрасывает их state
        async with conn.transaction():
            for task in plan.tasks:
                await repository.enqueue(account_id=account_id, spec=task)
    finally:
        await conn.close()

    return HHSourceSeedSummary(
        generation_id=str(generation_id),
        account_key=account.key,
        profile_key=account.profile,
        kind=kind.value,
        search_tasks=len(plan.search_tasks),
        resume_tasks=len(plan.resume_tasks),
        ensured_tasks=len(plan.tasks),
    )
