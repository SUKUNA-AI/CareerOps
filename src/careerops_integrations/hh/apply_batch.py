"""Compatibility-oriented APPLY pipeline, isolated from the OBSERVE implementation."""

from __future__ import annotations

import asyncio
import random
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from careerops_storage import S3JsonStore

from .application_audit import (
    HHApplicationAuditService,
    HHApplicationBlocked,
    HHApplicationUncertain,
)
from .application_claims import ApplicationClaimStore
from .cover_letters import build_cover_letter
from .driver import HHApplicantToolCLI
from .filtering import prefilter_ml_search_item, validate_ml_vacancy
from .runtime import HHExternalWriteGuard, RuntimeMode

DEFAULT_ML_SEARCH = (
    'NAME:("ML Engineer" OR "ML-инженер" OR "Machine Learning" OR '
    '"Data Scientist" OR "Data Science" OR "AI Engineer" OR "AI-инженер" OR '
    '"AI разработчик" OR "AI-разработчик" OR "Computer Vision" OR "CV Engineer" OR '
    '"NLP Engineer" OR "NLP-инженер" OR "LLM Engineer" OR "VLM Engineer" OR '
    '"MLOps" OR "ML Infrastructure" OR "DL Engineer" OR "DL-инженер" OR '
    '"DL-исследователь")'
)


def _now() -> datetime:
    return datetime.now(UTC)


def _require_published_resume(
    payload: dict[str, Any],
    *,
    resume_id: str,
) -> None:
    """Fail closed unless the current upstream resume is explicitly published."""

    status = payload.get("status")
    if isinstance(status, dict):
        status = status.get("id")
    if status != "published":
        raise ValueError(
            f"resume {resume_id!r} is not currently published; "
            f"upstream_status={status!r}"
        )


async def _write_json(
    store: S3JsonStore,
    key: str,
    payload: object,
    *,
    collected_at: datetime | None = None,
) -> str:
    return (await store.put_json(key, payload, collected_at=collected_at)).uri


@dataclass(frozen=True, slots=True)
class ApplyBatchOptions:
    """Resolved legacy APPLY settings plus optional dynamic binding metadata."""

    config_dir: Path
    profile: str
    resume_id: str
    external_write_guard: HHExternalWriteGuard
    claim_store: ApplicationClaimStore
    account_key: str | None = None
    resume_key: str | None = None
    target_key: str | None = None
    binding_version: int | None = None
    query_sets: tuple[str, ...] = ()
    resume_reconciliation_audit: dict[str, Any] | None = None
    letter_file: Path | None = None
    search: str = DEFAULT_ML_SEARCH
    area: int = 1
    period: int = 14
    pages: int = 1
    per_page: int = 50
    max_responses: int = 15
    professional_roles: tuple[int, ...] = ()
    min_delay: float = 1.0
    max_delay: float = 3.0

    def validate(self) -> None:
        """Reject invalid limits and fail before constructing write-capable services."""

        self.external_write_guard.validate_write_capable_startup()
        if self.external_write_guard.runtime_mode is not RuntimeMode.APPLY:
            raise ValueError("ApplyBatchOptions requires runtime_mode=apply")
        if not self.resume_id.strip():
            raise ValueError("resume_id must not be empty")
        if self.max_responses < 1:
            raise ValueError("max_responses must be >= 1")
        if self.min_delay < 0 or self.max_delay < self.min_delay:
            raise ValueError("invalid delay range")


async def run_apply_batch(
    store: S3JsonStore,
    options: ApplyBatchOptions,
    *,
    driver: HHApplicantToolCLI | None = None,
) -> dict[str, Any]:
    """Run the existing filtering/letter/application behavior in explicit APPLY."""

    options.validate()
    fixed_message: str | None = None
    if options.letter_file is not None:
        fixed_message = options.letter_file.read_text(encoding="utf-8").strip()
        if not fixed_message:
            raise ValueError("Letter file is empty")

    driver = driver or HHApplicantToolCLI(
        config_dir=options.config_dir,
        profile=options.profile,
        external_write_guard=options.external_write_guard,
    )
    try:
        resume: dict[str, object] = driver.fetch_resume(options.resume_id)
    except Exception as exc:
        raise RuntimeError(
            "could not verify current HH resume publication status; APPLY aborted"
        ) from exc
    _require_published_resume(resume, resume_id=options.resume_id)
    application_service = HHApplicationAuditService(
        driver=driver,
        store=store,
        claim_store=options.claim_store,
        account_key=options.account_key or options.profile,
        profile_id=options.profile,
        external_write_guard=options.external_write_guard,
        application_context={
            "account_key": options.account_key,
            "resume_key": options.resume_key,
            "target_key": options.target_key,
            "binding_version": options.binding_version,
            "source_profile": options.profile,
            "source_resume_id": options.resume_id,
            "query_sets": list(options.query_sets),
        },
    )

    run_id = uuid4()
    started_at = _now()
    run_prefix = f"batches/date={started_at.date().isoformat()}/run_id={run_id}"
    await _write_json(
        store,
        f"{run_prefix}/run.json",
        {
            "event_type": "hh.batch.started",
            "schema_version": 2,
            "run_id": str(run_id),
            "runtime_mode": RuntimeMode.APPLY.value,
            "account_key": options.account_key,
            "profile_id": options.profile,
            "resume_id": options.resume_id,
            "resume_key": options.resume_key,
            "target_key": options.target_key,
            "binding_version": options.binding_version,
            "query_sets": list(options.query_sets),
            "search": options.search,
            "area": options.area,
            "period": options.period,
            "pages": options.pages,
            "per_page": options.per_page,
            "professional_roles": list(options.professional_roles),
            "max_responses": options.max_responses,
            "cover_letter_mode": (
                "fixed_file" if fixed_message is not None else "vacancy_template_v1"
            ),
            "live": True,
            "external_writes_allowed": True,
            "started_at": started_at.isoformat(),
        },
    )
    if options.resume_reconciliation_audit is not None:
        await _write_json(
            store,
            f"{run_prefix}/resume_reconciliation.json",
            options.resume_reconciliation_audit,
        )

    discovered = driver.search_vacancies(
        text=options.search,
        area=options.area,
        period=options.period,
        order_by="publication_time",
        per_page=options.per_page,
        pages=options.pages,
        professional_roles=list(options.professional_roles) or None,
    )
    await _write_json(
        store,
        f"{run_prefix}/discovery.json",
        {
            "event_type": "hh.batch.discovery",
            "schema_version": 2,
            "run_id": str(run_id),
            "count": len(discovered),
            "items": discovered,
            "created_at": _now().isoformat(),
        },
    )

    reasons: Counter[str] = Counter()
    prefiltered = 0
    fetched = 0
    accepted = 0
    submitted = 0
    confirmed = 0
    failed = 0
    external_writes_attempted = 0
    stopped_on_captcha = False

    for search_item in discovered:
        # A failed/ambiguous employer-facing call still consumes the conservative
        # account quota.  This prevents unlimited write attempts when HH errors.
        if external_writes_attempted >= options.max_responses:
            break
        vacancy_id = str(search_item.get("id") or "")
        if not vacancy_id:
            continue
        candidate_prefix = f"{run_prefix}/candidates/vacancy_id={vacancy_id}"
        title = str(search_item.get("name") or "")
        search_item_uri = await _write_json(
            store,
            f"{candidate_prefix}/search_item.json",
            search_item,
            collected_at=_now(),
        )

        predecision = prefilter_ml_search_item(search_item)
        if not predecision.accepted:
            prefiltered += 1
            reason = f"prefilter_{predecision.reason}"
            reasons[reason] += 1
            await _write_json(
                store,
                f"{candidate_prefix}/decision.json",
                {
                    "event_type": "hh.vacancy.decision",
                    "schema_version": 2,
                    "stage": "search_item_prefilter",
                    "run_id": str(run_id),
                    "vacancy_id": vacancy_id,
                    "vacancy_title": title,
                    **predecision.to_dict(),
                    "reason": reason,
                    "search_item_uri": search_item_uri,
                    "created_at": _now().isoformat(),
                },
            )
            continue

        try:
            vacancy = driver.fetch_vacancy(vacancy_id)
            fetched += 1
        except Exception as exc:  # noqa: BLE001 - audit boundary
            text = str(exc)
            captcha = "captcha_required" in text
            failed += 1
            reason = "captcha_required" if captcha else "fetch_failed"
            reasons[reason] += 1
            await _write_json(
                store,
                f"{candidate_prefix}/decision.json",
                {
                    "event_type": "hh.vacancy.decision",
                    "schema_version": 2,
                    "stage": "full_fetch",
                    "run_id": str(run_id),
                    "vacancy_id": vacancy_id,
                    "vacancy_title": title,
                    "accepted": False,
                    "reason": reason,
                    "error_type": type(exc).__name__,
                    "error": text,
                    "search_item_uri": search_item_uri,
                    "created_at": _now().isoformat(),
                },
            )
            if captcha:
                stopped_on_captcha = True
                break
            continue

        vacancy_uri = await _write_json(
            store,
            f"{candidate_prefix}/vacancy.json",
            vacancy,
            collected_at=_now(),
        )
        decision = validate_ml_vacancy(vacancy, required_area_id=options.area)
        submission_mode = (
            "upstream_hh_test" if vacancy.get("has_test") else "negotiations_api"
        )
        reasons[decision.reason] += 1
        decision_uri = await _write_json(
            store,
            f"{candidate_prefix}/decision.json",
            {
                "event_type": "hh.vacancy.decision",
                "schema_version": 2,
                "stage": "full_vacancy_validation",
                "run_id": str(run_id),
                "vacancy_id": vacancy_id,
                "vacancy_title": vacancy.get("name"),
                "company_name": (vacancy.get("employer") or {}).get("name"),
                "submission_mode": submission_mode,
                "has_test": bool(vacancy.get("has_test")),
                **decision.to_dict(),
                "search_item_uri": search_item_uri,
                "vacancy_uri": vacancy_uri,
                "created_at": _now().isoformat(),
            },
        )
        title = str(vacancy.get("name") or "")
        if not decision.accepted:
            print(f"SKIP {vacancy_id}: {title} [{decision.reason}]")
            continue
        accepted += 1

        if fixed_message is not None:
            message = fixed_message
            cover_letter_payload = {
                "event_type": "hh.cover_letter.generated",
                "schema_version": 1,
                "run_id": str(run_id),
                "vacancy_id": vacancy_id,
                "strategy": "fixed_file",
                "template_id": "fixed",
                "matched_domains": list(decision.matched_domains),
                "matched_skills": [],
                "message": message,
                "created_at": _now().isoformat(),
            }
        else:
            cover_letter = build_cover_letter(
                vacancy=vacancy,
                resume=resume,
                matched_domains=decision.matched_domains,
                seed=f"{run_id}:{vacancy_id}",
            )
            message = cover_letter.message
            cover_letter_payload = {
                "event_type": "hh.cover_letter.generated",
                "schema_version": 1,
                "run_id": str(run_id),
                "vacancy_id": vacancy_id,
                **cover_letter.to_dict(),
                "created_at": _now().isoformat(),
            }
        cover_letter_uri = await _write_json(
            store,
            f"{candidate_prefix}/cover_letter.json",
            cover_letter_payload,
        )

        if external_writes_attempted:
            await asyncio.sleep(random.uniform(options.min_delay, options.max_delay))
        try:
            result = await application_service.apply(
                vacancy_id=vacancy_id,
                resume_id=options.resume_id,
                message=message,
                before=vacancy,
            )
        except HHApplicationBlocked as exc:
            reasons["blocked_after_recheck"] += 1
            print(f"SKIP {vacancy_id}: recheck blocked: {exc}")
            continue
        except HHApplicationUncertain as exc:
            external_writes_attempted += int(exc.external_write_attempted)
            failed += 1
            reasons["application_uncertain"] += 1
            await _write_json(
                store,
                f"{candidate_prefix}/outcome.json",
                {
                    "event_type": "hh.batch.application_failed",
                    "schema_version": 2,
                    "run_id": str(run_id),
                    "vacancy_id": vacancy_id,
                    "status": "uncertain",
                    "reason": "application_uncertain",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "external_write_attempted": exc.external_write_attempted,
                    "created_at": _now().isoformat(),
                },
            )
            continue
        except Exception as exc:  # noqa: BLE001 - batch should continue
            failed += 1
            text = str(exc)
            captcha = "captcha_required" in text
            reason = "captcha_required" if captcha else "application_failed"
            reasons[reason] += 1
            await _write_json(
                store,
                f"{candidate_prefix}/outcome.json",
                {
                    "event_type": "hh.batch.application_failed",
                    "schema_version": 2,
                    "run_id": str(run_id),
                    "vacancy_id": vacancy_id,
                    "status": "failed",
                    "reason": reason,
                    "error_type": type(exc).__name__,
                    "error": text,
                    "created_at": _now().isoformat(),
                },
            )
            if captcha:
                stopped_on_captcha = True
                break
            continue

        external_writes_attempted += 1
        submitted += 1
        confirmed += int(result.confirmed)
        await _write_json(
            store,
            f"{candidate_prefix}/outcome.json",
            {
                "event_type": "hh.batch.application_completed",
                "schema_version": 2,
                "run_id": str(run_id),
                "vacancy_id": vacancy_id,
                "status": result.status,
                "confirmed": result.confirmed,
                "submission_mode": result.submission_mode,
                "application_run_id": str(result.run_id),
                "application_result_uri": result.result_uri,
                "cover_letter_uri": cover_letter_uri,
                "created_at": _now().isoformat(),
            },
        )
        print(
            f"APPLY {vacancy_id}: {title} "
            f"[{result.submission_mode}, {result.status}, confirmed={result.confirmed}]"
        )
        print(f"AUDIT {decision_uri}")

    summary: dict[str, Any] = {
        "event_type": "hh.batch.finished",
        "schema_version": 2,
        "run_id": str(run_id),
        "runtime_mode": RuntimeMode.APPLY.value,
        "account_key": options.account_key,
        "live": True,
        "discovered": len(discovered),
        "prefiltered": prefiltered,
        "full_fetched": fetched,
        "accepted": accepted,
        "submitted": submitted,
        "confirmed": confirmed,
        "external_writes_attempted": external_writes_attempted,
        "quota_consumed": max(submitted, external_writes_attempted),
        "failed": failed,
        "stopped_on_captcha": stopped_on_captcha,
        "reasons": dict(sorted(reasons.items())),
        "finished_at": _now().isoformat(),
        "s3_prefix": run_prefix,
    }
    summary_uri = await _write_json(store, f"{run_prefix}/summary.json", summary)
    summary["summary_uri"] = summary_uri
    return summary
