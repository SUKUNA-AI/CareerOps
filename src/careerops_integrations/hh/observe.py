"""Broad account-scoped HH OBSERVE pipeline and replayable S3 RAW v3 audit."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from .configuration import DiscoveryConfig, DiscoveryQuery, HHAccountConfig
from .driver import HHVacancySearchPage
from .resume_sync import (
    ReconciledResume,
    ResumeReconciliationResult,
    resume_vacancy_dedup_key,
)
from .runtime import HHExternalWriteGuard, RuntimeMode


class ObserveObjectRef(Protocol):
    """Minimal immutable-object reference returned by the S3 writer."""

    @property
    def uri(self) -> str:
        """Return the canonical object URI."""

        ...


class ObserveJsonStore(Protocol):
    """Asynchronous JSON persistence required by OBSERVE."""

    async def put_json(
        self,
        key: str,
        payload: Any,
        *,
        collected_at: datetime | None = None,
    ) -> ObserveObjectRef:
        """Write one JSON object and provenance metadata."""

        ...


class ObserveDriver(Protocol):
    """Read-only subset of the existing HH transport used by OBSERVE."""

    def search_vacancy_pages(
        self,
        *,
        text: str,
        area: int = 1,
        period: int = 14,
        order_by: str = "publication_time",
        per_page: int = 50,
        pages: int = 1,
        professional_roles: list[int] | None = None,
    ) -> Iterable[HHVacancySearchPage]:
        """Return exact HH query pages in source order."""

        ...

    def fetch_vacancy(self, vacancy_id: str | int) -> dict[str, Any]:
        """Fetch one exact full HH vacancy object."""

        ...


@dataclass(frozen=True, slots=True)
class ObserveQueryCursorReservation:
    """Atomically reserved deterministic slice of one profile's query catalog."""

    source_profile: str
    account_key: str
    catalog_signature: str
    catalog_size: int
    window_start: int
    window_size: int
    next_query_offset: int

    @property
    def wrapped(self) -> bool:
        """Report whether this reservation reached or crossed catalog end."""

        return self.window_start + self.window_size >= self.catalog_size


class ObserveQueryCursorStore(Protocol):
    """Persistent atomic query-window reservation used by every OBSERVE run."""

    async def reserve(
        self,
        *,
        source_profile: str,
        account_key: str,
        catalog_signature: str,
        catalog_size: int,
        max_queries: int,
        run_id: UUID,
        reserved_at: datetime,
    ) -> ObserveQueryCursorReservation:
        """Reserve and advance one profile cursor before issuing search requests."""

        ...


@dataclass(slots=True)
class _Candidate:
    vacancy_id: str
    canonical_search_item: dict[str, Any]
    matched_query_keys: list[str] = field(default_factory=list)
    matched_query_sets: list[str] = field(default_factory=list)
    query_page_uris: list[str] = field(default_factory=list)

    def add_observation(
        self,
        *,
        query_key: str,
        query_set_key: str,
        query_page_uri: str,
    ) -> None:
        """Retain complete ordered query provenance without duplicating values."""

        if query_key not in self.matched_query_keys:
            self.matched_query_keys.append(query_key)
        if query_set_key not in self.matched_query_sets:
            self.matched_query_sets.append(query_set_key)
        if query_page_uri not in self.query_page_uris:
            self.query_page_uris.append(query_page_uri)


@dataclass(frozen=True, slots=True)
class ObserveRunResult:
    """Machine-readable summary and pause signal returned to CLI/scheduler."""

    run_id: UUID
    account_key: str
    run_prefix: str
    summary: dict[str, Any]


Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _captcha_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "captcha_required" in text or "captcha" in text


def query_catalog_signature(queries: tuple[DiscoveryQuery, ...]) -> str:
    """Hash ordered stable query keys so catalog edits reset rotation safely."""

    encoded = json.dumps(
        [query.spec.key for query in queries],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def select_reserved_queries(
    queries: tuple[DiscoveryQuery, ...],
    reservation: ObserveQueryCursorReservation,
) -> tuple[DiscoveryQuery, ...]:
    """Select one circular ordered window and reject inconsistent cursor state."""

    if reservation.catalog_size != len(queries):
        raise RuntimeError("OBSERVE query cursor catalog size mismatch")
    if reservation.catalog_signature != query_catalog_signature(queries):
        raise RuntimeError("OBSERVE query cursor catalog signature mismatch")
    if not 0 <= reservation.window_start < reservation.catalog_size:
        raise RuntimeError("OBSERVE query cursor start is outside the catalog")
    if not 1 <= reservation.window_size <= reservation.catalog_size:
        raise RuntimeError("OBSERVE query cursor window size is invalid")
    expected_next = (
        reservation.window_start + reservation.window_size
    ) % reservation.catalog_size
    if reservation.next_query_offset != expected_next:
        raise RuntimeError("OBSERVE query cursor next offset is inconsistent")
    return tuple(
        queries[(reservation.window_start + offset) % reservation.catalog_size]
        for offset in range(reservation.window_size)
    )


def _query_params(
    query: DiscoveryQuery,
    discovery: DiscoveryConfig,
) -> dict[str, Any]:
    defaults = discovery.defaults
    spec = query.spec
    return {
        "text": spec.text,
        "area": spec.area or defaults.area,
        "period": spec.period or defaults.period,
        "order_by": defaults.order_by,
        "per_page": spec.per_page or defaults.per_page,
        "pages": spec.pages or defaults.pages,
        "professional_roles": None,
    }


def _evaluation_candidates(
    *,
    account_key: str,
    candidate: _Candidate,
    resumes: tuple[ReconciledResume, ...],
    query_set_by_key: dict[str, str],
    full_fetch_status: str,
) -> list[dict[str, Any]]:
    """Materialize auditable vacancy x resume work items without a relevance decision.

    Filtering v2 is intentionally out of scope.  These records only prove why a
    bound active resume is a future evaluation candidate. Query-set overlap is
    evidence only and never gates the account-wide vacancy x resume fan-out.
    """

    evaluations: list[dict[str, Any]] = []
    for resume in resumes:
        resume_query_sets = set(resume.query_sets)
        overlap_query_sets = [
            key for key in candidate.matched_query_sets if key in resume_query_sets
        ]
        overlap_query_keys = [
            key
            for key in candidate.matched_query_keys
            if query_set_by_key.get(key) in resume_query_sets
        ]
        source_resume_id, vacancy_id = resume_vacancy_dedup_key(
            resume.source_resume_id,
            candidate.vacancy_id,
        )
        evaluations.append(
            {
                "event_type": "hh.vacancy_resume.evaluation_candidate",
                "schema_version": 1,
                "account_key": account_key,
                "source_profile": resume.source_profile,
                "source_resume_id": source_resume_id,
                "vacancy_id": vacancy_id,
                "duplicate_key": {
                    "account_key": account_key,
                    "source_profile": resume.source_profile,
                    "source_resume_id": source_resume_id,
                    "vacancy_id": vacancy_id,
                },
                "binding_key": resume.binding_key,
                "target_key": resume.target_key,
                "binding_version": resume.binding_version,
                "auto_apply": resume.auto_apply,
                "matched_query_keys": list(candidate.matched_query_keys),
                "matched_query_sets": list(candidate.matched_query_sets),
                "resume_query_sets": list(resume.query_sets),
                "provenance_overlap": {
                    "has_overlap": bool(overlap_query_sets),
                    "matched_query_keys": overlap_query_keys,
                    "matched_query_sets": overlap_query_sets,
                },
                "full_fetch_status": full_fetch_status,
                "evaluation_status": "pending_filtering_v2",
            }
        )
    return evaluations


class HHObserveRunner:
    """Run broad discovery without importing or invoking application/filtering code."""

    def __init__(
        self,
        *,
        driver: ObserveDriver,
        store: ObserveJsonStore,
        account: HHAccountConfig,
        discovery: DiscoveryConfig,
        resume_reconciliation: ResumeReconciliationResult,
        query_cursor_store: ObserveQueryCursorStore,
        external_write_guard: HHExternalWriteGuard,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = _utc_now,
        rng: random.Random | None = None,
    ) -> None:
        """Bind one account run to read-only transport and immutable storage."""

        if external_write_guard.runtime_mode is not RuntimeMode.OBSERVE:
            raise ValueError("HHObserveRunner requires runtime_mode=observe")
        if external_write_guard.external_writes_allowed:
            raise ValueError("OBSERVE must never receive an external-write capability")
        inventory = resume_reconciliation.inventory
        if inventory.account_key != account.key or inventory.source_profile != account.profile:
            raise ValueError("resume reconciliation identity does not match account")
        self.driver = driver
        self.store = store
        self.account = account
        self.discovery = discovery
        self.resume_reconciliation = resume_reconciliation
        self.query_cursor_store = query_cursor_store
        self.external_write_guard = external_write_guard
        self.sleep = sleep
        self.clock = clock
        self.rng = rng or random.SystemRandom()

    async def _put(
        self,
        key: str,
        payload: Any,
        *,
        source_payload: bool = False,
    ) -> str:
        """Persist JSON; source bodies receive observation time only in S3 metadata."""

        collected_at = self.clock() if source_payload else None
        ref = await self.store.put_json(
            key,
            payload,
            collected_at=collected_at,
        )
        return ref.uri

    async def run(self, *, run_id: UUID | None = None) -> ObserveRunResult:
        """Search, union, deduplicate, full-fetch, and persist one account run."""

        run_id = run_id or uuid4()
        started_at = self.clock().astimezone(UTC)
        run_prefix = (
            f"batches/date={started_at.date().isoformat()}/run_id={run_id}"
        )
        query_set_keys = self.account.query_set_keys
        catalog_queries = self.discovery.select_queries(query_set_keys)
        if not catalog_queries:
            raise ValueError("OBSERVE account query catalog must not be empty")
        query_reservation = await self.query_cursor_store.reserve(
            source_profile=self.account.profile,
            account_key=self.account.key,
            catalog_signature=query_catalog_signature(catalog_queries),
            catalog_size=len(catalog_queries),
            max_queries=self.discovery.defaults.max_queries_per_run,
            run_id=run_id,
            reserved_at=started_at,
        )
        queries = select_reserved_queries(catalog_queries, query_reservation)
        query_set_by_key = {
            query.spec.key: query.query_set_key for query in queries
        }
        inventory = self.resume_reconciliation.inventory
        evaluation_resumes = inventory.evaluation_resumes

        await self._put(
            f"{run_prefix}/run.json",
            {
                "event_type": "hh.batch.started",
                "schema_version": 3,
                "run_id": str(run_id),
                "runtime_mode": RuntimeMode.OBSERVE.value,
                "account_key": self.account.key,
                "profile_id": self.account.profile,
                "enabled_resume_keys": [
                    resume.binding_key
                    for resume in evaluation_resumes
                    if resume.binding_key is not None
                ],
                "source_resume_ids": [
                    resume.source_resume_id for resume in evaluation_resumes
                ],
                "target_keys": [
                    resume.target_key
                    for resume in evaluation_resumes
                    if resume.target_key is not None
                ],
                "active_bindings": [
                    {
                        "source_resume_id": resume.source_resume_id,
                        "binding_key": resume.binding_key,
                        "target_key": resume.target_key,
                        "binding_version": resume.binding_version,
                        "query_sets": list(resume.query_sets),
                        "auto_apply": resume.auto_apply,
                    }
                    for resume in evaluation_resumes
                ],
                "query_set_keys": list(query_set_keys),
                "query_keys": [query.spec.key for query in queries],
                "query_catalog_size": query_reservation.catalog_size,
                "query_catalog_signature": query_reservation.catalog_signature,
                "max_queries_per_run": self.discovery.defaults.max_queries_per_run,
                "query_cursor_start": query_reservation.window_start,
                "query_cursor_next": query_reservation.next_query_offset,
                "query_rotation_wrapped": query_reservation.wrapped,
                "discovery_config_version": self.discovery.schema_version,
                "area": self.discovery.defaults.area,
                "period": self.discovery.defaults.period,
                "pages": self.discovery.defaults.pages,
                "per_page": self.discovery.defaults.per_page,
                "max_unique_vacancies_per_run": (
                    self.discovery.defaults.max_unique_vacancies_per_run
                ),
                "max_full_fetch_per_run": (
                    self.discovery.defaults.max_full_fetch_per_run
                ),
                "search_query_delay_seconds": (
                    self.discovery.defaults.search_query_delay_seconds
                ),
                "full_fetch_min_delay_seconds": (
                    self.discovery.defaults.full_fetch_min_delay_seconds
                ),
                "full_fetch_max_delay_seconds": (
                    self.discovery.defaults.full_fetch_max_delay_seconds
                ),
                "started_at": started_at.isoformat(),
                "external_writes_allowed": False,
            },
        )
        await self._put(
            f"{run_prefix}/resume_reconciliation.json",
            self.resume_reconciliation.audit_payload(),
        )

        candidates: dict[str, _Candidate] = {}
        query_page_refs: list[dict[str, Any]] = []
        queries_executed: list[str] = []
        query_errors: list[dict[str, str]] = []
        search_observation_count = 0
        stopped_on_captcha = False

        for query_index, query in enumerate(queries):
            if stopped_on_captcha:
                break
            if query_index and self.discovery.defaults.search_query_delay_seconds:
                await self.sleep(self.discovery.defaults.search_query_delay_seconds)
            queries_executed.append(query.spec.key)
            try:
                pages = iter(
                    self.driver.search_vacancy_pages(
                        **_query_params(query, self.discovery)
                    )
                )
            except Exception as exc:  # noqa: BLE001 - audit query failure and isolate account
                captcha = _captcha_error(exc)
                query_errors.append(
                    {
                        "query_key": query.spec.key,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "reason": "captcha_required" if captcha else "search_failed",
                    }
                )
                stopped_on_captcha = captcha
                continue

            while True:
                try:
                    page = next(pages)
                except StopIteration:
                    break
                except Exception as exc:  # noqa: BLE001 - preserve already-written pages
                    captcha = _captcha_error(exc)
                    query_errors.append(
                        {
                            "query_key": query.spec.key,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                            "reason": (
                                "captcha_required" if captcha else "search_failed"
                            ),
                        }
                    )
                    stopped_on_captcha = captcha
                    break
                page_key = (
                    f"{run_prefix}/discovery/queries/"
                    f"query={query.spec.key}/page={page.page:03d}.json"
                )
                page_uri = await self._put(
                    page_key,
                    page.payload,
                    source_payload=True,
                )
                query_page_refs.append(
                    {
                        "query_key": query.spec.key,
                        "query_set": query.query_set_key,
                        "page": page.page,
                        "uri": page_uri,
                    }
                )
                items = page.payload.get("items")
                if items is None:
                    items = []
                if not isinstance(items, list):
                    query_errors.append(
                        {
                            "query_key": query.spec.key,
                            "error_type": "InvalidSearchPayload",
                            "error": f"page {page.page} has non-list items",
                            "reason": "invalid_search_payload",
                        }
                    )
                    break
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    vacancy_id = str(item.get("id") or "").strip()
                    if not vacancy_id:
                        continue
                    search_observation_count += 1
                    candidate = candidates.get(vacancy_id)
                    if candidate is None:
                        candidate = _Candidate(
                            vacancy_id=vacancy_id,
                            canonical_search_item=deepcopy(item),
                        )
                        candidates[vacancy_id] = candidate
                    candidate.add_observation(
                        query_key=query.spec.key,
                        query_set_key=query.query_set_key,
                        query_page_uri=page_uri,
                    )

        all_candidates = list(candidates.values())
        max_unique = self.discovery.defaults.max_unique_vacancies_per_run
        selected_candidates = all_candidates[:max_unique]
        await self._put(
            f"{run_prefix}/discovery.json",
            {
                "event_type": "hh.batch.discovery",
                "schema_version": 3,
                "run_id": str(run_id),
                "account_key": self.account.key,
                "queries_executed": queries_executed,
                "query_catalog_size": query_reservation.catalog_size,
                "query_catalog_signature": query_reservation.catalog_signature,
                "max_queries_per_run": self.discovery.defaults.max_queries_per_run,
                "query_cursor_start": query_reservation.window_start,
                "query_cursor_next": query_reservation.next_query_offset,
                "query_rotation_wrapped": query_reservation.wrapped,
                "query_sets": list(query_set_keys),
                "query_pages": query_page_refs,
                "query_errors": query_errors,
                "search_observation_count": search_observation_count,
                "unique_vacancy_count": len(all_candidates),
                "selected_candidate_count": len(selected_candidates),
                "truncated_by_max_unique": len(selected_candidates) < len(all_candidates),
                "vacancies": [
                    {
                        "vacancy_id": candidate.vacancy_id,
                        "matched_query_keys": candidate.matched_query_keys,
                        "matched_query_sets": candidate.matched_query_sets,
                        "query_page_uris": candidate.query_page_uris,
                    }
                    for candidate in all_candidates
                ],
                "created_at": self.clock().astimezone(UTC).isoformat(),
            },
        )

        reasons: Counter[str] = Counter()
        full_fetch_attempted = 0
        full_fetched = 0
        failed = len(query_errors)
        account_paused = stopped_on_captcha
        max_fetch = self.discovery.defaults.max_full_fetch_per_run
        evaluation_candidate_count = 0

        for candidate_index, candidate in enumerate(selected_candidates):
            candidate_prefix = (
                f"{run_prefix}/candidates/vacancy_id={candidate.vacancy_id}"
            )
            search_item_uri = await self._put(
                f"{candidate_prefix}/search_item.json",
                candidate.canonical_search_item,
                source_payload=True,
            )
            vacancy_uri: str | None = None
            full_fetch_status = "not_attempted"
            full_fetch_error: dict[str, str] | None = None

            if account_paused:
                full_fetch_status = "account_paused"
                reasons["account_paused"] += 1
            elif full_fetch_attempted >= max_fetch:
                full_fetch_status = "technical_limit"
                reasons["technical_limit"] += 1
            else:
                if candidate_index and self.discovery.defaults.full_fetch_max_delay_seconds:
                    delay = self.rng.uniform(
                        self.discovery.defaults.full_fetch_min_delay_seconds,
                        self.discovery.defaults.full_fetch_max_delay_seconds,
                    )
                    if delay:
                        await self.sleep(delay)
                full_fetch_attempted += 1
                try:
                    vacancy = self.driver.fetch_vacancy(candidate.vacancy_id)
                except Exception as exc:  # noqa: BLE001 - one source failure is sidecar data
                    captcha = _captcha_error(exc)
                    full_fetch_status = "failed"
                    reason = "captcha_required" if captcha else "fetch_failed"
                    reasons[reason] += 1
                    failed += 1
                    full_fetch_error = {
                        "reason": reason,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                    if captcha:
                        account_paused = True
                        stopped_on_captcha = True
                else:
                    vacancy_uri = await self._put(
                        f"{candidate_prefix}/vacancy.json",
                        vacancy,
                        source_payload=True,
                    )
                    full_fetch_status = "fetched"
                    full_fetched += 1
                    reasons["fetched"] += 1

            evaluation_candidates = (
                _evaluation_candidates(
                    account_key=self.account.key,
                    candidate=candidate,
                    resumes=evaluation_resumes,
                    query_set_by_key=query_set_by_key,
                    full_fetch_status=full_fetch_status,
                )
                if full_fetch_status == "fetched"
                else []
            )
            evaluation_candidate_count += len(evaluation_candidates)
            evaluation_candidates_uri = await self._put(
                f"{candidate_prefix}/evaluation_candidates.json",
                {
                    "event_type": "hh.vacancy_resume.evaluation_candidates",
                    "schema_version": 1,
                    "run_id": str(run_id),
                    "runtime_mode": RuntimeMode.OBSERVE.value,
                    "account_key": self.account.key,
                    "source_profile": self.account.profile,
                    "vacancy_id": candidate.vacancy_id,
                    "evaluation_semantics": "routing_only_no_relevance_decision",
                    "evaluations": evaluation_candidates,
                    "created_at": self.clock().astimezone(UTC).isoformat(),
                },
            )
            observation_payload: dict[str, Any] = {
                "event_type": "hh.vacancy.observed",
                "schema_version": 3,
                "run_id": str(run_id),
                "runtime_mode": RuntimeMode.OBSERVE.value,
                "account_key": self.account.key,
                "profile_id": self.account.profile,
                "vacancy_id": candidate.vacancy_id,
                "candidate_resume_keys": [
                    evaluation["binding_key"]
                    for evaluation in evaluation_candidates
                    if evaluation["binding_key"] is not None
                ],
                "candidate_source_resume_ids": [
                    evaluation["source_resume_id"]
                    for evaluation in evaluation_candidates
                ],
                "candidate_target_keys": [
                    evaluation["target_key"]
                    for evaluation in evaluation_candidates
                    if evaluation["target_key"] is not None
                ],
                "candidate_bindings": [
                    {
                        "source_resume_id": evaluation["source_resume_id"],
                        "binding_key": evaluation["binding_key"],
                        "target_key": evaluation["target_key"],
                        "binding_version": evaluation["binding_version"],
                        "auto_apply": evaluation["auto_apply"],
                    }
                    for evaluation in evaluation_candidates
                ],
                "evaluation_candidate_count": len(evaluation_candidates),
                "evaluation_candidates_uri": evaluation_candidates_uri,
                "matched_query_keys": candidate.matched_query_keys,
                "matched_query_sets": candidate.matched_query_sets,
                "query_page_uris": candidate.query_page_uris,
                "search_item_uri": search_item_uri,
                "vacancy_uri": vacancy_uri,
                "full_fetch_status": full_fetch_status,
                "observed_at": self.clock().astimezone(UTC).isoformat(),
            }
            if full_fetch_error is not None:
                observation_payload["full_fetch_error"] = full_fetch_error
            await self._put(
                f"{candidate_prefix}/observation.json",
                observation_payload,
            )

        finished_at = self.clock().astimezone(UTC)
        summary: dict[str, Any] = {
            "event_type": "hh.batch.finished",
            "schema_version": 3,
            "run_id": str(run_id),
            "runtime_mode": RuntimeMode.OBSERVE.value,
            "account_key": self.account.key,
            "profile_id": self.account.profile,
            "queries_executed": len(queries_executed),
            "queries_selected": len(queries),
            "query_catalog_size": query_reservation.catalog_size,
            "query_catalog_signature": query_reservation.catalog_signature,
            "query_cursor_start": query_reservation.window_start,
            "query_cursor_next": query_reservation.next_query_offset,
            "query_rotation_wrapped": query_reservation.wrapped,
            "search_observation_count": search_observation_count,
            "unique_vacancy_count": len(all_candidates),
            "candidate_count": len(selected_candidates),
            "full_fetch_attempted": full_fetch_attempted,
            "full_fetched": full_fetched,
            "evaluation_candidate_count": evaluation_candidate_count,
            "submitted": 0,
            "confirmed": 0,
            "external_writes_attempted": 0,
            "failed": failed,
            "stopped_on_captcha": stopped_on_captcha,
            "reasons": dict(sorted(reasons.items())),
            "finished_at": finished_at.isoformat(),
            "s3_prefix": run_prefix,
        }
        summary_uri = await self._put(f"{run_prefix}/summary.json", summary)
        summary["summary_uri"] = summary_uri
        return ObserveRunResult(
            run_id=run_id,
            account_key=self.account.key,
            run_prefix=run_prefix,
            summary=summary,
        )
