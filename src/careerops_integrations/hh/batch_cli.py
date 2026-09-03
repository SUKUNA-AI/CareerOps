"""Two-mode HH batch CLI: broad OBSERVE by default, guarded APPLY by opt-in."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from careerops_storage import (
    PostgresApplicationClaimStore,
    PostgresObserveQueryCursorStore,
    PostgresResumeRegistry,
    PostgresSettings,
    S3JsonStore,
    S3Settings,
    connect_postgres,
)

from .application_claims import ApplicationClaimStore
from .apply_batch import DEFAULT_ML_SEARCH, ApplyBatchOptions, run_apply_batch
from .configuration import (
    DiscoveryConfig,
    HHAccountConfig,
    HHAccountsConfig,
    accounts_config_path_from_env,
    discovery_config_path_from_env,
    load_accounts_config,
    load_discovery_config,
)
from .driver import HHApplicantToolCLI
from .observe import HHObserveRunner, ObserveQueryCursorStore
from .resume_sync import (
    JsonResumeRegistry,
    ReconciledResume,
    ResumeReconciliationResult,
    ResumeRegistry,
    reconcile_account_resumes,
    resume_state_dir_from_env,
)
from .runtime import HHExternalWriteGuard, RuntimeMode

SUMMARY_PREFIX = "CAREEROPS_SUMMARY_JSON="


async def _write_json(
    store: S3JsonStore,
    key: str,
    payload: object,
    *,
    collected_at: datetime | None = None,
) -> str:
    """Persist JSON unchanged and return its immutable S3 URI."""

    return (
        await store.put_json(
            key,
            payload,
            collected_at=collected_at,
        )
    ).uri


def _parser() -> argparse.ArgumentParser:
    """Build the preferred account CLI plus temporary manual APPLY compatibility."""

    parser = argparse.ArgumentParser(
        description="CareerOPS HH OBSERVE/APPLY account runner with immutable S3 audit"
    )
    parser.add_argument(
        "--mode",
        help="observe (default) or apply; invalid values fail closed",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Deprecated alias for --mode apply. It never bypasses "
            "CAREEROPS_HH_ALLOW_EXTERNAL_WRITES."
        ),
    )
    parser.add_argument("--account-key")
    parser.add_argument(
        "--resume-key",
        help="Explicit configured binding key for multi-resume APPLY accounts",
    )
    parser.add_argument(
        "--accounts-config",
        type=Path,
        default=accounts_config_path_from_env(),
    )
    parser.add_argument(
        "--discovery-config",
        type=Path,
        default=discovery_config_path_from_env(),
    )
    parser.add_argument(
        "--resume-registry",
        choices=("postgres", "json"),
        default=os.getenv("CAREEROPS_HH_RESUME_REGISTRY", "postgres"),
        help=(
            "PostgreSQL is primary; json only replaces resume inventory for "
            "dev/bootstrap, while OBSERVE query rotation remains PostgreSQL-backed"
        ),
    )
    parser.add_argument(
        "--resume-state-dir",
        type=Path,
        default=resume_state_dir_from_env(),
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("hh-applicant-tool/config"),
    )

    # Temporary manual/debug APPLY compatibility. Production account dispatch does
    # not pass a static resume id or profile through scheduler arguments.
    parser.add_argument("--profile", default="careerops-ml")
    parser.add_argument("--resume-id")
    parser.add_argument("--letter-file", type=Path)
    parser.add_argument("--search", default=DEFAULT_ML_SEARCH)
    parser.add_argument("--area", type=int, default=1)
    parser.add_argument("--period", type=int, default=14)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--per-page", type=int, default=50)
    parser.add_argument("--max-responses", type=int, default=15)
    parser.add_argument(
        "--account-quota-remaining",
        type=int,
        help=(
            "Scheduler-supplied remaining APPLY quota for this account/day. "
            "Ignored nowhere and never accepted by OBSERVE."
        ),
    )
    parser.add_argument(
        "--professional-role",
        type=int,
        action="append",
        dest="professional_roles",
    )
    parser.add_argument("--min-delay", type=float, default=1.0)
    parser.add_argument("--max-delay", type=float, default=3.0)
    return parser


def _load_configs_if_available(
    args: argparse.Namespace,
) -> tuple[DiscoveryConfig | None, HHAccountsConfig | None]:
    """Load account topology when present while preserving manual APPLY debug use."""

    if not args.accounts_config.exists():
        return None, None
    discovery = load_discovery_config(args.discovery_config)
    accounts = load_accounts_config(args.accounts_config, discovery=discovery)
    return discovery, accounts


def _resolve_mode(
    args: argparse.Namespace,
    accounts: HHAccountsConfig | None,
) -> RuntimeMode:
    """Resolve CLI/env/config mode with OBSERVE as the final default."""

    configured: str | RuntimeMode | None = args.mode
    if configured is None:
        configured = os.getenv("CAREEROPS_HH_MODE")
    if configured is None and accounts is not None:
        configured = accounts.runtime_mode
    mode = RuntimeMode.parse(configured)
    if args.live:
        if args.mode is not None and mode is not RuntimeMode.APPLY:
            raise ValueError("--live conflicts with --mode observe")
        mode = RuntimeMode.APPLY
    return mode


async def _reconcile(
    *,
    account: HHAccountConfig,
    driver: HHApplicantToolCLI,
    registry: ResumeRegistry,
) -> ResumeReconciliationResult:
    """Use the existing account-scoped transport for authoritative resume sync."""

    return await reconcile_account_resumes(
        driver=driver,
        account=account,
        registry=registry,
    )


def _select_apply_resumes(
    *,
    reconciliation: ResumeReconciliationResult,
    resume_key: str | None,
) -> tuple[ReconciledResume, ...]:
    """Select active, assigned, explicit-auto-apply identities for an account run."""

    candidates = reconciliation.inventory.auto_apply_resumes
    if resume_key is not None:
        for resume in candidates:
            if resume.binding_key == resume_key:
                return (resume,)
        raise ValueError(
            f"resume binding {resume_key!r} is not active and auto_apply-enabled"
        )
    return candidates


def _account_run_quota(
    *,
    configured_daily_cap: int,
    requested_max_responses: int,
    scheduler_remaining: int | None,
) -> tuple[int, int]:
    """Return authoritative remaining daily quota and conservative run budget."""

    if requested_max_responses < 1:
        raise ValueError("max_responses must be >= 1")
    if scheduler_remaining is not None and scheduler_remaining < 0:
        raise ValueError("account_quota_remaining must be >= 0")
    remaining = configured_daily_cap
    if scheduler_remaining is not None:
        remaining = min(remaining, scheduler_remaining)
    return remaining, min(remaining, requested_max_responses)


async def _run_observe(
    *,
    args: argparse.Namespace,
    store: S3JsonStore,
    accounts: HHAccountsConfig | None,
    discovery: DiscoveryConfig | None,
    guard: HHExternalWriteGuard,
    registry: ResumeRegistry,
    query_cursor_store: ObserveQueryCursorStore,
) -> dict[str, Any]:
    if accounts is None or discovery is None:
        raise ValueError(
            f"OBSERVE requires account config {args.accounts_config} and "
            f"discovery config {args.discovery_config}"
        )
    if not args.account_key:
        raise ValueError("OBSERVE requires --account-key")
    if args.resume_id is not None or args.resume_key is not None:
        raise ValueError("OBSERVE resolves dynamic resumes and does not accept resume selectors")
    if args.account_quota_remaining is not None:
        raise ValueError("OBSERVE does not accept application quota arguments")

    account = accounts.resolve_account(args.account_key)
    driver = HHApplicantToolCLI(
        config_dir=args.config_dir,
        profile=account.profile,
        external_write_guard=guard,
    )
    reconciliation = await _reconcile(
        account=account,
        driver=driver,
        registry=registry,
    )
    result = await HHObserveRunner(
        driver=driver,
        store=store,
        account=account,
        discovery=discovery,
        resume_reconciliation=reconciliation,
        query_cursor_store=query_cursor_store,
        external_write_guard=guard,
    ).run()
    return result.summary


async def _run_apply(
    *,
    args: argparse.Namespace,
    store: S3JsonStore,
    accounts: HHAccountsConfig | None,
    guard: HHExternalWriteGuard,
    registry: ResumeRegistry,
    claim_store: ApplicationClaimStore,
) -> dict[str, Any]:
    guard.validate_write_capable_startup()
    resolved_driver: HHApplicantToolCLI | None = None
    if args.account_key:
        if args.account_quota_remaining is None:
            raise ValueError(
                "account APPLY requires --account-quota-remaining from the "
                "account-scoped scheduler; use manual --resume-id only for debug"
            )
        if accounts is None:
            raise ValueError(f"APPLY account config was not found: {args.accounts_config}")
        account = accounts.resolve_account(args.account_key)
        resolved_driver = HHApplicantToolCLI(
            config_dir=args.config_dir,
            profile=account.profile,
            external_write_guard=guard,
        )
        reconciliation = await _reconcile(
            account=account,
            driver=resolved_driver,
            registry=registry,
        )
        resumes = _select_apply_resumes(
            reconciliation=reconciliation,
            resume_key=args.resume_key,
        )
        quota_remaining, run_quota = _account_run_quota(
            configured_daily_cap=account.apply_daily_cap,
            requested_max_responses=min(args.max_responses, account.max_apply_per_run),
            scheduler_remaining=args.account_quota_remaining,
        )
        account_run_id = uuid4()
        started_at = datetime.now(UTC)
        account_run_prefix = (
            f"account-runs/date={started_at.date().isoformat()}/"
            f"run_id={account_run_id}"
        )
        selected_bindings = [
            {
                "source_profile": account.profile,
                "source_resume_id": resume.source_resume_id,
                "binding_key": resume.binding_key,
                "target_key": resume.target_key,
                "binding_version": resume.binding_version,
                "query_sets": list(resume.query_sets),
                "auto_apply": resume.auto_apply,
            }
            for resume in resumes
        ]
        await _write_json(
            store,
            f"{account_run_prefix}/run.json",
            {
                "event_type": "hh.account.apply.started",
                "schema_version": 1,
                "run_id": str(account_run_id),
                "runtime_mode": RuntimeMode.APPLY.value,
                "account_key": account.key,
                "source_profile": account.profile,
                "apply_runs_per_day": account.apply_runs_per_day,
                "configured_daily_cap": account.apply_daily_cap,
                "max_apply_per_run": account.max_apply_per_run,
                "scheduler_quota_remaining": args.account_quota_remaining,
                "account_quota_remaining_before_run": quota_remaining,
                "effective_run_quota": run_quota,
                "selected_bindings": selected_bindings,
                "started_at": started_at.isoformat(),
                "external_writes_allowed": True,
            },
        )
        await _write_json(
            store,
            f"{account_run_prefix}/resume_reconciliation.json",
            reconciliation.audit_payload(),
        )

        totals: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        quota_consumed = 0
        stopped_on_captcha = False
        resume_runs: list[dict[str, Any]] = []
        for resume in resumes:
            if quota_consumed >= run_quota:
                break
            resume_quota = run_quota - quota_consumed
            options = ApplyBatchOptions(
                config_dir=args.config_dir,
                profile=account.profile,
                resume_id=resume.source_resume_id,
                account_key=account.key,
                claim_store=claim_store,
                resume_key=resume.binding_key,
                target_key=resume.target_key,
                binding_version=resume.binding_version,
                query_sets=resume.query_sets,
                resume_reconciliation_audit=reconciliation.audit_payload(),
                external_write_guard=guard,
                letter_file=args.letter_file,
                search=args.search,
                area=args.area,
                period=args.period,
                pages=args.pages,
                per_page=args.per_page,
                max_responses=resume_quota,
                professional_roles=tuple(args.professional_roles or ()),
                min_delay=args.min_delay,
                max_delay=args.max_delay,
            )
            try:
                child_summary = await run_apply_batch(
                    store,
                    options,
                    driver=resolved_driver,
                )
            except Exception as exc:
                await _write_json(
                    store,
                    f"{account_run_prefix}/failure.json",
                    {
                        "event_type": "hh.account.apply.failed",
                        "schema_version": 1,
                        "run_id": str(account_run_id),
                        "account_key": account.key,
                        "source_profile": account.profile,
                        "source_resume_id": resume.source_resume_id,
                        "binding_key": resume.binding_key,
                        "target_key": resume.target_key,
                        "binding_version": resume.binding_version,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "failed_at": datetime.now(UTC).isoformat(),
                    },
                )
                raise

            child_consumed = int(
                child_summary.get(
                    "quota_consumed",
                    max(
                        int(child_summary.get("submitted", 0)),
                        int(child_summary.get("external_writes_attempted", 0)),
                    ),
                )
            )
            if not 0 <= child_consumed <= resume_quota:
                raise RuntimeError(
                    "APPLY child summary exceeded its account quota allocation"
                )
            quota_consumed += child_consumed
            for key in (
                "discovered",
                "prefiltered",
                "full_fetched",
                "accepted",
                "submitted",
                "confirmed",
                "external_writes_attempted",
                "failed",
            ):
                totals[key] += int(child_summary.get(key, 0))
            reasons.update(child_summary.get("reasons") or {})
            stopped_on_captcha = bool(child_summary.get("stopped_on_captcha"))
            resume_runs.append(
                {
                    "source_profile": account.profile,
                    "source_resume_id": resume.source_resume_id,
                    "binding_key": resume.binding_key,
                    "target_key": resume.target_key,
                    "binding_version": resume.binding_version,
                    "allocated_quota": resume_quota,
                    "quota_consumed": child_consumed,
                    "submitted": int(child_summary.get("submitted", 0)),
                    "confirmed": int(child_summary.get("confirmed", 0)),
                    "external_writes_attempted": int(
                        child_summary.get("external_writes_attempted", 0)
                    ),
                    "batch_run_id": child_summary.get("run_id"),
                    "batch_summary_uri": child_summary.get("summary_uri"),
                    "stopped_on_captcha": stopped_on_captcha,
                }
            )
            if stopped_on_captcha:
                break

        finished_at = datetime.now(UTC)
        summary: dict[str, Any] = {
            "event_type": "hh.account.apply.finished",
            "schema_version": 1,
            "run_id": str(account_run_id),
            "runtime_mode": RuntimeMode.APPLY.value,
            "account_key": account.key,
            "source_profile": account.profile,
            "apply_runs_per_day": account.apply_runs_per_day,
            "configured_daily_cap": account.apply_daily_cap,
            "max_apply_per_run": account.max_apply_per_run,
            "scheduler_quota_remaining": args.account_quota_remaining,
            "account_quota_remaining_before_run": quota_remaining,
            "effective_run_quota": run_quota,
            "quota_consumed": quota_consumed,
            "account_quota_remaining_after_run": max(
                0,
                quota_remaining - quota_consumed,
            ),
            "eligible_resume_count": len(resumes),
            "evaluated_resume_count": len(resume_runs),
            "resume_runs": resume_runs,
            "discovered": totals["discovered"],
            "prefiltered": totals["prefiltered"],
            "full_fetched": totals["full_fetched"],
            "accepted": totals["accepted"],
            "submitted": totals["submitted"],
            "confirmed": totals["confirmed"],
            "external_writes_attempted": totals["external_writes_attempted"],
            "failed": totals["failed"],
            "stopped_on_captcha": stopped_on_captcha,
            "reasons": dict(sorted(reasons.items())),
            "status": (
                "no_auto_apply_bindings"
                if not resumes
                else "quota_exhausted"
                if run_quota == 0
                else "completed"
            ),
            "finished_at": finished_at.isoformat(),
            "s3_prefix": account_run_prefix,
        }
        summary_uri = await _write_json(
            store,
            f"{account_run_prefix}/summary.json",
            summary,
        )
        summary["summary_uri"] = summary_uri
        return summary
    else:
        if args.resume_key is not None:
            raise ValueError("--resume-key requires --account-key")
        if args.account_quota_remaining is not None:
            raise ValueError("--account-quota-remaining requires --account-key")
        if not args.resume_id:
            raise ValueError(
                "manual APPLY compatibility requires --resume-id; production should use "
                "--account-key and reconciled bindings"
            )
        options = ApplyBatchOptions(
            config_dir=args.config_dir,
            profile=args.profile,
            resume_id=args.resume_id,
            external_write_guard=guard,
            claim_store=claim_store,
            letter_file=args.letter_file,
            search=args.search,
            area=args.area,
            period=args.period,
            pages=args.pages,
            per_page=args.per_page,
            max_responses=args.max_responses,
            professional_roles=tuple(args.professional_roles or ()),
            min_delay=args.min_delay,
            max_delay=args.max_delay,
        )
    return await run_apply_batch(store, options, driver=resolved_driver)


async def _run(
    args: argparse.Namespace,
    store: S3JsonStore,
    *,
    registry: ResumeRegistry,
    claim_store: ApplicationClaimStore | None = None,
    query_cursor_store: ObserveQueryCursorStore | None = None,
) -> dict[str, Any]:
    """Resolve the canonical mode and execute exactly one isolated pipeline."""

    discovery, accounts = _load_configs_if_available(args)
    mode = _resolve_mode(args, accounts)
    guard = HHExternalWriteGuard.from_env(mode)
    if mode is RuntimeMode.OBSERVE:
        if query_cursor_store is None:
            raise RuntimeError(
                "OBSERVE requires persistent PostgreSQL query rotation state"
            )
        summary = await _run_observe(
            args=args,
            store=store,
            accounts=accounts,
            discovery=discovery,
            guard=guard,
            registry=registry,
            query_cursor_store=query_cursor_store,
        )
    else:
        if claim_store is None:
            raise RuntimeError("APPLY requires persistent PostgreSQL application claims")
        summary = await _run_apply(
            args=args,
            store=store,
            accounts=accounts,
            guard=guard,
            registry=registry,
            claim_store=claim_store,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(SUMMARY_PREFIX + json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return summary


async def _async_main() -> None:
    args = _parser().parse_args()
    try:
        async with S3JsonStore(S3Settings.from_env()) as store:
            connection = await connect_postgres(
                PostgresSettings.from_env(),
                autocommit=True,
            )
            async with connection:
                registry: ResumeRegistry = (
                    PostgresResumeRegistry(connection)
                    if args.resume_registry == "postgres"
                    else JsonResumeRegistry(args.resume_state_dir)
                )
                await _run(
                    args,
                    store,
                    registry=registry,
                    claim_store=PostgresApplicationClaimStore(connection),
                    query_cursor_store=PostgresObserveQueryCursorStore(connection),
                )
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc


def main() -> None:
    """Run one account through OBSERVE or explicitly guarded APPLY."""

    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
