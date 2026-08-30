from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from careerops_storage import S3JsonStore, S3Settings

from .application_audit import HHApplicationAuditService, HHApplicationBlocked
from .driver import HHApplicantToolCLI
from .filtering import prefilter_ml_search_item, validate_ml_vacancy


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


def _write_json(store: S3JsonStore, key: str, payload: object) -> str:
    return store.put_json(key, payload).uri


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CareerOPS ML/DS/AI batch application runner with strict validation and S3 audit"
    )
    parser.add_argument("--config-dir", type=Path, default=Path("hh-applicant-tool/config"))
    parser.add_argument("--profile", default="careerops-ml")
    parser.add_argument("--resume-id", required=True)
    parser.add_argument("--letter-file", type=Path, required=True)
    parser.add_argument("--search", default=DEFAULT_ML_SEARCH)
    parser.add_argument("--area", type=int, default=1)
    parser.add_argument("--period", type=int, default=14)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--per-page", type=int, default=50)
    parser.add_argument("--max-responses", type=int, default=15)
    parser.add_argument(
        "--professional-role",
        type=int,
        action="append",
        dest="professional_roles",
        help="Repeat to add HH professional_role filters. Omit for title-only discovery.",
    )
    parser.add_argument("--min-delay", type=float, default=1.0)
    parser.add_argument("--max-delay", type=float, default=3.0)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually submit accepted vacancies. Without this flag only validation/audit runs.",
    )
    args = parser.parse_args()

    if args.max_responses < 1:
        raise SystemExit("--max-responses must be >= 1")
    if args.min_delay < 0 or args.max_delay < args.min_delay:
        raise SystemExit("invalid delay range")

    message = args.letter_file.read_text(encoding="utf-8").strip()
    if not message:
        raise SystemExit("Letter file is empty")

    store = S3JsonStore(S3Settings.from_env())
    driver = HHApplicantToolCLI(config_dir=args.config_dir, profile=args.profile)
    application_service = HHApplicationAuditService(
        driver=driver,
        store=store,
        profile_id=args.profile,
    )

    run_id = uuid4()
    started_at = _now()
    run_prefix = f"batches/date={started_at.date().isoformat()}/run_id={run_id}"

    _write_json(
        store,
        f"{run_prefix}/run.json",
        {
            "event_type": "hh.batch.started",
            "schema_version": 2,
            "run_id": str(run_id),
            "profile_id": args.profile,
            "resume_id": args.resume_id,
            "search": args.search,
            "area": args.area,
            "period": args.period,
            "pages": args.pages,
            "per_page": args.per_page,
            "professional_roles": args.professional_roles or [],
            "max_responses": args.max_responses,
            "live": args.live,
            "started_at": started_at.isoformat(),
        },
    )

    discovered = driver.search_vacancies(
        text=args.search,
        area=args.area,
        period=args.period,
        order_by="publication_time",
        per_page=args.per_page,
        pages=args.pages,
        professional_roles=args.professional_roles,
    )

    _write_json(
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
    stopped_on_captcha = False

    for search_item in discovered:
        if args.live and submitted >= args.max_responses:
            break

        vacancy_id = str(search_item.get("id") or "")
        if not vacancy_id:
            continue

        candidate_prefix = f"{run_prefix}/candidates/vacancy_id={vacancy_id}"
        title = str(search_item.get("name") or "")

        search_item_uri = _write_json(
            store,
            f"{candidate_prefix}/search_item.json",
            search_item,
        )

        predecision = prefilter_ml_search_item(search_item)
        if not predecision.accepted:
            prefiltered += 1
            reason = f"prefilter_{predecision.reason}"
            reasons[reason] += 1
            _write_json(
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
            print(f"PREFILTER SKIP {vacancy_id}: {title} [{predecision.reason}]")
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
            _write_json(
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
                print(f"STOP {vacancy_id}: HH captcha_required; ending batch without more API calls")
                break
            print(f"ERROR {vacancy_id}: fetch_failed: {exc}")
            continue

        vacancy_uri = _write_json(store, f"{candidate_prefix}/vacancy.json", vacancy)
        decision = validate_ml_vacancy(vacancy, required_area_id=args.area)
        submission_mode = (
            "upstream_hh_test" if vacancy.get("has_test") else "negotiations_api"
        )
        reasons[decision.reason] += 1
        decision_uri = _write_json(
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
        if not args.live:
            print(f"PASS {vacancy_id}: {title} [{submission_mode}] -> {decision_uri}")
            continue

        if submitted:
            time.sleep(random.uniform(args.min_delay, args.max_delay))

        try:
            result = application_service.apply(
                vacancy_id=vacancy_id,
                resume_id=args.resume_id,
                message=message,
            )
        except HHApplicationBlocked as exc:
            reasons["blocked_after_recheck"] += 1
            print(f"SKIP {vacancy_id}: recheck blocked: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - batch should continue
            failed += 1
            text = str(exc)
            captcha = "captcha_required" in text
            reason = "captcha_required" if captcha else "application_failed"
            reasons[reason] += 1
            _write_json(
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
                print(f"STOP {vacancy_id}: HH captcha_required during apply; ending batch")
                break
            print(f"ERROR {vacancy_id}: application_failed: {exc}")
            continue

        submitted += 1
        confirmed += int(result.confirmed)
        _write_json(
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
                "created_at": _now().isoformat(),
            },
        )
        print(f"APPLY {vacancy_id}: {title} [{result.submission_mode}, {result.status}, confirmed={result.confirmed}]")

    summary = {
        "event_type": "hh.batch.finished",
        "schema_version": 2,
        "run_id": str(run_id),
        "live": args.live,
        "discovered": len(discovered),
        "prefiltered": prefiltered,
        "full_fetched": fetched,
        "accepted": accepted,
        "submitted": submitted,
        "confirmed": confirmed,
        "failed": failed,
        "stopped_on_captcha": stopped_on_captcha,
        "reasons": dict(sorted(reasons.items())),
        "finished_at": _now().isoformat(),
        "s3_prefix": run_prefix,
    }
    summary_uri = _write_json(store, f"{run_prefix}/summary.json", summary)
    summary["summary_uri"] = summary_uri
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
