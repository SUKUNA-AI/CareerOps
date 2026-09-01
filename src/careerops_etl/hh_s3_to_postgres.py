"""Validate and load immutable HH S3 audits into PostgreSQL current state."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from careerops_contracts import RawVacancyRef
from careerops_integrations.hh.mapper import extract_operational, map_hh_vacancy
from careerops_storage import S3ObjectRef

_BATCH_OBJECT_KEY = re.compile(
    r"^batches/date=(?P<date>[^/]+)/run_id=(?P<run_id>[^/]+)/"
    r"(?P<name>run|summary)\.json$"
)
_CANDIDATE_OBJECT_KEY = re.compile(
    r"^(?P<prefix>batches/date=[^/]+/run_id=[^/]+)/candidates/"
    r"vacancy_id=(?P<vacancy_id>[^/]+)/"
    r"(?P<name>search_item|vacancy|decision|outcome)\.json$"
)
_APPLICATION_RESULT_KEY = re.compile(
    r"^applications/date=(?P<date>[^/]+)/run_id=(?P<run_id>[^/]+)/"
    r"vacancy_id=(?P<vacancy_id>[^/]+)/application_result\.json$"
)


def _normalize_aware(value: datetime, field_name: str) -> datetime:
    """Require a timezone and normalize one source timestamp to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _parse_hh_datetime(value: Any, field_name: str) -> datetime | None:
    """Parse HH ISO timestamps, including offsets written without a colon."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO datetime string")
    normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc
    return _normalize_aware(parsed, field_name)


class S3ReadStore(Protocol):
    """Minimal asynchronous S3 reader required by the ETL."""

    def iter_keys(self, prefix: str = "") -> AsyncIterator[str]:
        """Yield relative object keys below a prefix."""

        ...

    def relative_key(self, key: str) -> str:
        """Normalize a full key or URI to the configured relative key."""

        ...

    async def get_json_with_metadata(self, key: str) -> tuple[Any, S3ObjectRef]:
        """Read one JSON value and its verified provenance metadata."""

        ...


class HHOLTPSink(Protocol):
    """Async normalized persistence operations used by the ETL."""

    async def upsert_source_profile(self, **kwargs: Any) -> int:
        """Persist the source profile and return its database id."""

        ...

    async def upsert_resume(self, **kwargs: Any) -> int:
        """Persist the resume and return its database id."""

        ...

    async def upsert_partial_vacancy(self, **kwargs: Any) -> int:
        """Persist fields proven by search_item.json."""

        ...

    async def upsert_vacancy(self, **kwargs: Any) -> int:
        """Persist fully mapped vacancy state."""

        ...

    async def upsert_batch_run(self, **kwargs: Any) -> UUID:
        """Persist or finalize a batch run."""

        ...

    async def upsert_vacancy_decision(self, **kwargs: Any) -> int:
        """Persist one normalized candidate decision."""

        ...

    async def upsert_application(self, **kwargs: Any) -> int:
        """Persist one fully proven completed application."""

        ...


@dataclass(frozen=True, slots=True)
class HHBatchLocation:
    """Parsed S3 location and completion marker for one HH batch."""

    run_id: UUID
    batch_date: date
    prefix: str
    has_summary: bool

    @property
    def run_key(self) -> str:
        """Return the relative run.json key."""

        return f"{self.prefix}/run.json"

    @property
    def summary_key(self) -> str:
        """Return the relative summary.json key."""

        return f"{self.prefix}/summary.json"

    @property
    def candidates_prefix(self) -> str:
        """Return the relative prefix containing candidate objects."""

        return f"{self.prefix}/candidates"


@dataclass(frozen=True, slots=True)
class HHCandidateLocation:
    """Discovered S3 object keys belonging to one vacancy candidate."""

    vacancy_id: str
    prefix: str
    search_item_key: str | None = None
    vacancy_key: str | None = None
    decision_key: str | None = None
    outcome_key: str | None = None


class HHBatchStart(BaseModel):
    """Strict schema_version=2 contract for run.json."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: Literal["hh.batch.started"]
    schema_version: Literal[2]
    run_id: UUID
    profile_id: str = Field(min_length=1)
    resume_id: str = Field(min_length=1)
    search: str | None = None
    area: int | None = None
    period: int | None = None
    pages: int | None = None
    per_page: int | None = None
    professional_roles: list[int] = Field(default_factory=list)
    max_responses: int | None = None
    cover_letter_mode: str | None = None
    live: bool
    started_at: datetime

    @field_validator("started_at")
    @classmethod
    def normalize_started_at(cls, value: datetime) -> datetime:
        """Normalize the batch start timestamp to UTC."""

        return _normalize_aware(value, "started_at")


class HHBatchSummary(BaseModel):
    """Strict schema_version=2 contract for summary.json."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: Literal["hh.batch.finished"]
    schema_version: Literal[2]
    run_id: UUID
    live: bool
    discovered: int = Field(ge=0)
    prefiltered: int = Field(ge=0)
    full_fetched: int = Field(ge=0)
    accepted: int = Field(ge=0)
    submitted: int = Field(ge=0)
    confirmed: int = Field(ge=0)
    failed: int = Field(ge=0)
    stopped_on_captcha: bool
    reasons: dict[str, int]
    finished_at: datetime
    s3_prefix: str

    @field_validator("finished_at")
    @classmethod
    def normalize_finished_at(cls, value: datetime) -> datetime:
        """Normalize the batch finish timestamp to UTC."""

        return _normalize_aware(value, "finished_at")


class HHVacancyDecision(BaseModel):
    """Validated decision.json fields required by normalized persistence."""

    model_config = ConfigDict(extra="allow", frozen=True)

    event_type: Literal["hh.vacancy.decision"]
    schema_version: Literal[2]
    run_id: UUID
    vacancy_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    accepted: bool
    reason: str = Field(min_length=1)
    matched_domains: list[str] = Field(default_factory=list)
    blocked_terms: list[str] = Field(default_factory=list)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        """Normalize the decision timestamp to UTC."""

        return _normalize_aware(value, "created_at")


class HHBatchOutcome(BaseModel):
    """Strict batch-level completed or failed application outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: Literal[
        "hh.batch.application_completed",
        "hh.batch.application_failed",
    ]
    schema_version: Literal[2]
    run_id: UUID
    vacancy_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    confirmed: bool | None = None
    submission_mode: str | None = None
    application_run_id: UUID | None = None
    application_result_uri: str | None = None
    cover_letter_uri: str | None = None
    reason: str | None = None
    error_type: str | None = None
    error: str | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        """Normalize the outcome timestamp to UTC."""

        return _normalize_aware(value, "created_at")


class HHApplicationRequest(BaseModel):
    """Validated application_request.json audit contract."""

    model_config = ConfigDict(extra="allow", frozen=True)

    event_type: Literal["hh.application.requested"]
    schema_version: Literal[2]
    run_id: UUID
    profile_id: str = Field(min_length=1)
    resume_id: str = Field(min_length=1)
    vacancy_id: str = Field(min_length=1)
    submission_mode: str = Field(min_length=1)
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def normalize_requested_at(cls, value: datetime) -> datetime:
        """Normalize the request timestamp to UTC."""

        return _normalize_aware(value, "requested_at")


class HHApplicationResult(BaseModel):
    """Validated completed application_result.json audit contract."""

    model_config = ConfigDict(extra="allow", frozen=True)

    event_type: Literal["hh.application.submitted"]
    schema_version: Literal[2]
    run_id: UUID
    profile_id: str = Field(min_length=1)
    resume_id: str = Field(min_length=1)
    vacancy_id: str = Field(min_length=1)
    submission_mode: str = Field(min_length=1)
    status: str = Field(min_length=1)
    confirmed: bool
    relations: list[str] = Field(default_factory=list)
    finished_at: datetime

    @field_validator("finished_at")
    @classmethod
    def normalize_finished_at(cls, value: datetime) -> datetime:
        """Normalize the application finish timestamp to UTC."""

        return _normalize_aware(value, "finished_at")


@dataclass(frozen=True, slots=True)
class LoadedBatchHeader:
    """Validated batch contract plus normalized database foreign keys."""

    batch: HHBatchStart
    run_id: UUID
    source_profile_id: int
    resume_id: int


@dataclass(frozen=True, slots=True)
class LoadedBatchResult:
    """Counts and completion status produced after loading one batch."""

    run_id: UUID
    complete: bool
    candidates: int
    decisions: int
    applications: int


async def _json_object(
    store: S3ReadStore,
    key: str,
) -> tuple[dict[str, Any], S3ObjectRef]:
    """Read one S3 object and require a JSON-object payload."""

    payload, ref = await store.get_json_with_metadata(key)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{key} must contain a JSON object")
    return payload, ref


def _observed_at(ref: S3ObjectRef) -> datetime:
    """Prefer S3 collected_at metadata, falling back for legacy RAW only."""

    if ref.collected_at is not None:
        return _normalize_aware(ref.collected_at, f"collected_at metadata for {ref.uri}")
    if ref.last_modified is None:
        raise RuntimeError(
            f"legacy S3 object {ref.uri} has neither collected_at nor LastModified"
        )
    return _normalize_aware(ref.last_modified, f"LastModified for {ref.uri}")


def _same_ids(
    *,
    key: str,
    path_run_id: UUID,
    payload_run_id: UUID,
    path_vacancy_id: str | None = None,
    payload_vacancy_id: str | None = None,
) -> None:
    """Reject disagreement between path identity and audit payload identity."""

    if payload_run_id != path_run_id:
        raise RuntimeError(
            f"run_id mismatch for {key}: path={path_run_id}, payload={payload_run_id}"
        )
    if path_vacancy_id is not None and payload_vacancy_id != path_vacancy_id:
        raise RuntimeError(
            f"vacancy_id mismatch for {key}: "
            f"path={path_vacancy_id!r}, payload={payload_vacancy_id!r}"
        )


async def discover_hh_batches(store: S3ReadStore) -> list[HHBatchLocation]:
    """Discover valid batch prefixes from paginated run/summary object keys."""

    objects: dict[str, set[str]] = {}
    identities: dict[str, tuple[UUID, date]] = {}

    async for key in store.iter_keys("batches"):
        match = _BATCH_OBJECT_KEY.match(str(key))
        if match is None:
            continue
        prefix = str(key).rsplit("/", 1)[0]
        try:
            run_id = UUID(match.group("run_id"))
            batch_date = date.fromisoformat(match.group("date"))
        except ValueError as exc:
            raise RuntimeError(f"malformed HH batch path: {key!r}") from exc
        objects.setdefault(prefix, set()).add(match.group("name"))
        identities[prefix] = (run_id, batch_date)

    found: dict[UUID, HHBatchLocation] = {}
    for prefix, names in objects.items():
        if "run" not in names:
            continue
        run_id, batch_date = identities[prefix]
        existing = found.get(run_id)
        if existing is not None and existing.prefix != prefix:
            raise RuntimeError(
                f"run_id {run_id} appears in multiple S3 prefixes: "
                f"{existing.prefix!r} and {prefix!r}"
            )
        found[run_id] = HHBatchLocation(
            run_id=run_id,
            batch_date=batch_date,
            prefix=prefix,
            has_summary="summary" in names,
        )

    return sorted(found.values(), key=lambda item: (item.batch_date, str(item.run_id)))


async def discover_hh_candidates(
    store: S3ReadStore,
    location: HHBatchLocation,
) -> list[HHCandidateLocation]:
    """Group candidate audit keys by path-proven vacancy id."""

    found: dict[str, dict[str, str]] = {}
    async for key in store.iter_keys(location.candidates_prefix):
        value = str(key)
        match = _CANDIDATE_OBJECT_KEY.match(value)
        if match is None or match.group("prefix") != location.prefix:
            continue
        vacancy_id = match.group("vacancy_id").strip()
        if not vacancy_id:
            raise RuntimeError(f"empty vacancy_id in candidate path: {key!r}")
        found.setdefault(vacancy_id, {})[match.group("name")] = value

    candidates = [
        HHCandidateLocation(
            vacancy_id=vacancy_id,
            prefix=f"{location.candidates_prefix}/vacancy_id={vacancy_id}",
            search_item_key=keys.get("search_item"),
            vacancy_key=keys.get("vacancy"),
            decision_key=keys.get("decision"),
            outcome_key=keys.get("outcome"),
        )
        for vacancy_id, keys in found.items()
    ]
    return sorted(candidates, key=lambda item: item.vacancy_id)


async def load_batch_start(
    store: S3ReadStore,
    location: HHBatchLocation,
) -> HHBatchStart:
    """Read and validate run.json against its S3 path."""

    payload, _ = await _json_object(store, location.run_key)
    batch = HHBatchStart.model_validate(payload)
    _same_ids(
        key=location.run_key,
        path_run_id=location.run_id,
        payload_run_id=batch.run_id,
    )
    if batch.started_at.date() != location.batch_date:
        raise RuntimeError(
            f"date mismatch for {location.run_key}: "
            f"path={location.batch_date}, payload={batch.started_at.date()}"
        )
    return batch


def _batch_upsert_kwargs(
    batch: HHBatchStart,
    *,
    resume_id: int,
    location: HHBatchLocation,
) -> dict[str, Any]:
    """Transform a validated header into shared batch-run UPSERT fields."""

    return {
        "run_id": batch.run_id,
        "resume_id": resume_id,
        "search_query": batch.search,
        "area_id": str(batch.area) if batch.area is not None else None,
        "period_days": batch.period,
        "pages": batch.pages,
        "per_page": batch.per_page,
        "max_responses": batch.max_responses,
        "professional_roles": [str(role) for role in batch.professional_roles],
        "cover_letter_mode": batch.cover_letter_mode,
        "live": batch.live,
        "started_at": batch.started_at,
        "s3_prefix": location.prefix,
    }


async def load_batch_header(
    store: S3ReadStore,
    sink: HHOLTPSink,
    location: HHBatchLocation,
) -> LoadedBatchHeader:
    """Persist source profile, resume, and an initially incomplete batch run."""

    batch = await load_batch_start(store, location)
    source_profile_id = await sink.upsert_source_profile(
        source="hh",
        profile_key=batch.profile_id,
    )
    resume_id = await sink.upsert_resume(
        source_profile_id=source_profile_id,
        source_resume_id=batch.resume_id,
        observed_at=batch.started_at,
    )
    persisted_run_id = await sink.upsert_batch_run(
        **_batch_upsert_kwargs(batch, resume_id=resume_id, location=location),
        status="incomplete",
    )
    if persisted_run_id != batch.run_id:
        raise RuntimeError(
            "persisted batch run id does not match source run id: "
            f"source={batch.run_id}, persisted={persisted_run_id}"
        )
    return LoadedBatchHeader(
        batch=batch,
        run_id=batch.run_id,
        source_profile_id=source_profile_id,
        resume_id=resume_id,
    )


def _optional_text(value: Any) -> str | None:
    """Normalize optional scalar source values to non-empty strings."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _validate_vacancy_payload(
    payload: dict[str, Any],
    *,
    key: str,
    path_vacancy_id: str,
) -> None:
    """Require the vacancy payload id to match its candidate path id."""

    payload_id = _optional_text(payload.get("id"))
    if payload_id != path_vacancy_id:
        raise RuntimeError(
            f"vacancy_id mismatch for {key}: "
            f"path={path_vacancy_id!r}, payload={payload_id!r}"
        )


async def _load_search_item(
    store: S3ReadStore,
    sink: HHOLTPSink,
    candidate: HHCandidateLocation,
) -> int:
    """Persist only current vacancy fields supported by search_item.json."""

    if candidate.search_item_key is None:
        raise RuntimeError(f"candidate {candidate.prefix} has no search_item.json")
    payload, ref = await _json_object(store, candidate.search_item_key)
    _validate_vacancy_payload(
        payload,
        key=candidate.search_item_key,
        path_vacancy_id=candidate.vacancy_id,
    )
    employer = payload.get("employer")
    employer = employer if isinstance(employer, dict) else {}
    area = payload.get("area")
    area = area if isinstance(area, dict) else {}
    return await sink.upsert_partial_vacancy(
        source="hh",
        source_entity_id=candidate.vacancy_id,
        source_employer_id=_optional_text(employer.get("id")),
        title=_optional_text(payload.get("name")),
        company_name=_optional_text(employer.get("name")),
        location=_optional_text(area.get("name")),
        source_url=_optional_text(payload.get("alternate_url")),
        published_at=_parse_hh_datetime(payload.get("published_at"), "published_at"),
        observed_at=_observed_at(ref),
        raw_uri=ref.uri,
        content_hash=ref.sha256,
    )


async def _load_full_vacancy(
    store: S3ReadStore,
    sink: HHOLTPSink,
    candidate: HHCandidateLocation,
) -> int | None:
    """Map and persist a full vacancy when vacancy.json exists."""

    if candidate.vacancy_key is None:
        return None
    payload, ref = await _json_object(store, candidate.vacancy_key)
    _validate_vacancy_payload(
        payload,
        key=candidate.vacancy_key,
        path_vacancy_id=candidate.vacancy_id,
    )
    raw = RawVacancyRef(
        source="hh",
        source_entity_id=candidate.vacancy_id,
        raw_uri=ref.uri,
        content_hash=ref.sha256,
        collected_at=_observed_at(ref),
    )
    vacancy = map_hh_vacancy(payload, raw=raw)
    operational = extract_operational(payload)
    employer = payload.get("employer")
    employer = employer if isinstance(employer, dict) else {}
    return await sink.upsert_vacancy(
        vacancy=vacancy,
        operational=operational,
        source_employer_id=_optional_text(employer.get("id")),
    )


async def _load_decision(
    store: S3ReadStore,
    sink: HHOLTPSink,
    location: HHBatchLocation,
    candidate: HHCandidateLocation,
    vacancy_id: int,
) -> bool:
    """Validate and persist decision.json when present."""

    if candidate.decision_key is None:
        return False
    payload, _ = await _json_object(store, candidate.decision_key)
    decision = HHVacancyDecision.model_validate(payload)
    _same_ids(
        key=candidate.decision_key,
        path_run_id=location.run_id,
        payload_run_id=decision.run_id,
        path_vacancy_id=candidate.vacancy_id,
        payload_vacancy_id=decision.vacancy_id,
    )
    metadata_keys = (
        "vacancy_title",
        "company_name",
        "submission_mode",
        "has_test",
        "search_item_uri",
        "vacancy_uri",
        "error_type",
        "error",
    )
    metadata = {key: payload[key] for key in metadata_keys if key in payload}
    await sink.upsert_vacancy_decision(
        run_id=decision.run_id,
        vacancy_id=vacancy_id,
        stage=decision.stage,
        accepted=decision.accepted,
        reason=decision.reason,
        matched_domains=decision.matched_domains,
        blocked_terms=decision.blocked_terms,
        metadata=metadata,
        created_at=decision.created_at,
    )
    return True


def _validate_application_vacancy(
    payload: dict[str, Any],
    *,
    key: str,
    vacancy_id: str,
) -> None:
    """Validate the vacancy id embedded in an application snapshot."""

    _validate_vacancy_payload(payload, key=key, path_vacancy_id=vacancy_id)


async def _load_completed_application(
    store: S3ReadStore,
    sink: HHOLTPSink,
    *,
    header: LoadedBatchHeader,
    candidate: HHCandidateLocation,
    vacancy_id: int,
    outcome: HHBatchOutcome,
) -> None:
    """Require all four application audit objects before persisting an application."""

    if (
        outcome.application_run_id is None
        or outcome.application_result_uri is None
        or outcome.submission_mode is None
        or outcome.confirmed is None
    ):
        raise RuntimeError(
            f"completed outcome {candidate.outcome_key} lacks application audit identifiers"
        )

    result_key = store.relative_key(outcome.application_result_uri)
    result_match = _APPLICATION_RESULT_KEY.match(result_key)
    if result_match is None:
        raise RuntimeError(
            f"invalid application_result_uri in {candidate.outcome_key}: "
            f"{outcome.application_result_uri!r}"
        )
    try:
        path_application_run_id = UUID(result_match.group("run_id"))
        date.fromisoformat(result_match.group("date"))
    except ValueError as exc:
        raise RuntimeError(f"malformed application audit path: {result_key!r}") from exc
    if path_application_run_id != outcome.application_run_id:
        raise RuntimeError(
            f"application run_id mismatch for {result_key}: "
            f"outcome={outcome.application_run_id}, path={path_application_run_id}"
        )
    if result_match.group("vacancy_id") != candidate.vacancy_id:
        raise RuntimeError(
            f"application vacancy_id mismatch for {result_key}: "
            f"candidate={candidate.vacancy_id!r}, path={result_match.group('vacancy_id')!r}"
        )

    audit_prefix = result_key.rsplit("/", 1)[0]
    request_key = f"{audit_prefix}/application_request.json"
    before_key = f"{audit_prefix}/vacancy_before.json"
    after_key = f"{audit_prefix}/vacancy_after.json"
    request_payload, request_ref = await _json_object(store, request_key)
    result_payload, result_ref = await _json_object(store, result_key)
    before_payload, before_ref = await _json_object(store, before_key)
    after_payload, after_ref = await _json_object(store, after_key)
    _validate_application_vacancy(
        before_payload,
        key=before_key,
        vacancy_id=candidate.vacancy_id,
    )
    _validate_application_vacancy(
        after_payload,
        key=after_key,
        vacancy_id=candidate.vacancy_id,
    )

    request = HHApplicationRequest.model_validate(request_payload)
    result = HHApplicationResult.model_validate(result_payload)
    batch = header.batch
    for key, application_run_id, profile_id, source_resume_id, source_vacancy_id in (
        (
            request_key,
            request.run_id,
            request.profile_id,
            request.resume_id,
            request.vacancy_id,
        ),
        (
            result_key,
            result.run_id,
            result.profile_id,
            result.resume_id,
            result.vacancy_id,
        ),
    ):
        if application_run_id != outcome.application_run_id:
            raise RuntimeError(f"application run_id mismatch in {key}")
        if profile_id != batch.profile_id or source_resume_id != batch.resume_id:
            raise RuntimeError(f"application profile/resume mismatch in {key}")
        if source_vacancy_id != candidate.vacancy_id:
            raise RuntimeError(f"application vacancy_id mismatch in {key}")

    if request.submission_mode != outcome.submission_mode:
        raise RuntimeError(f"application submission_mode mismatch in {candidate.outcome_key}")
    if result.submission_mode != outcome.submission_mode:
        raise RuntimeError(f"application submission_mode mismatch in {result_key}")
    if result.status != outcome.status or result.confirmed != outcome.confirmed:
        raise RuntimeError(f"application status mismatch in {candidate.outcome_key}")
    if result.finished_at < request.requested_at:
        raise RuntimeError(f"application timestamps are reversed in {result_key}")

    if outcome.cover_letter_uri is not None:
        store.relative_key(outcome.cover_letter_uri)
    await sink.upsert_application(
        application_run_id=outcome.application_run_id,
        batch_run_id=batch.run_id,
        vacancy_id=vacancy_id,
        resume_id=header.resume_id,
        submission_mode=result.submission_mode,
        status=result.status,
        confirmed=result.confirmed,
        requested_at=request.requested_at,
        finished_at=result.finished_at,
        cover_letter_uri=outcome.cover_letter_uri,
        request_uri=request_ref.uri,
        result_uri=result_ref.uri,
        before_uri=before_ref.uri,
        after_uri=after_ref.uri,
        upstream_metadata={"relations": result.relations},
    )


async def _load_outcome(
    store: S3ReadStore,
    sink: HHOLTPSink,
    *,
    header: LoadedBatchHeader,
    candidate: HHCandidateLocation,
    vacancy_id: int,
) -> bool:
    """Persist a provable completed outcome and ignore unprovable failures."""

    if candidate.outcome_key is None:
        return False
    payload, _ = await _json_object(store, candidate.outcome_key)
    outcome = HHBatchOutcome.model_validate(payload)
    _same_ids(
        key=candidate.outcome_key,
        path_run_id=header.run_id,
        payload_run_id=outcome.run_id,
        path_vacancy_id=candidate.vacancy_id,
        payload_vacancy_id=outcome.vacancy_id,
    )
    if outcome.event_type == "hh.batch.application_failed":
        return False
    await _load_completed_application(
        store,
        sink,
        header=header,
        candidate=candidate,
        vacancy_id=vacancy_id,
        outcome=outcome,
    )
    return True


async def _load_summary(
    store: S3ReadStore,
    location: HHBatchLocation,
    batch: HHBatchStart,
) -> HHBatchSummary:
    """Validate summary.json identity, ordering, and prefix consistency."""

    payload, _ = await _json_object(store, location.summary_key)
    summary = HHBatchSummary.model_validate(payload)
    _same_ids(
        key=location.summary_key,
        path_run_id=location.run_id,
        payload_run_id=summary.run_id,
    )
    if summary.live != batch.live:
        raise RuntimeError(f"live flag mismatch in {location.summary_key}")
    if summary.s3_prefix.strip("/") != location.prefix:
        raise RuntimeError(
            f"s3_prefix mismatch in {location.summary_key}: "
            f"path={location.prefix!r}, payload={summary.s3_prefix!r}"
        )
    if summary.finished_at < batch.started_at:
        raise RuntimeError(f"batch timestamps are reversed in {location.summary_key}")
    return summary


async def load_hh_batch(
    store: S3ReadStore,
    sink: HHOLTPSink,
    location: HHBatchLocation,
) -> LoadedBatchResult:
    """Load one batch sequentially inside its caller-owned transaction."""

    header = await load_batch_header(store, sink, location)
    candidates = await discover_hh_candidates(store, location)
    decisions = 0
    applications = 0
    for candidate in candidates:
        vacancy_id = await _load_search_item(store, sink, candidate)
        full_vacancy_id = await _load_full_vacancy(store, sink, candidate)
        if full_vacancy_id is not None and full_vacancy_id != vacancy_id:
            raise RuntimeError(
                f"partial/full vacancy IDs differ for {candidate.prefix}: "
                f"partial={vacancy_id}, full={full_vacancy_id}"
            )
        decisions += int(
            await _load_decision(store, sink, location, candidate, vacancy_id)
        )
        applications += int(
            await _load_outcome(
                store,
                sink,
                header=header,
                candidate=candidate,
                vacancy_id=vacancy_id,
            )
        )

    if location.has_summary:
        summary = await _load_summary(store, location, header.batch)
        await sink.upsert_batch_run(
            **_batch_upsert_kwargs(
                header.batch,
                resume_id=header.resume_id,
                location=location,
            ),
            status="finished",
            finished_at=summary.finished_at,
            discovered=summary.discovered,
            prefiltered=summary.prefiltered,
            full_fetched=summary.full_fetched,
            accepted=summary.accepted,
            submitted=summary.submitted,
            confirmed=summary.confirmed,
            failed=summary.failed,
            stopped_on_captcha=summary.stopped_on_captcha,
        )

    return LoadedBatchResult(
        run_id=header.run_id,
        complete=location.has_summary,
        candidates=len(candidates),
        decisions=decisions,
        applications=applications,
    )
