"""Generate one globally spaced, interleaved multi-account HH plan."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from careerops_integrations.hh.configuration import (
    HHAccountConfig,
    HHAccountsConfig,
    load_accounts_config,
    load_discovery_config,
)

from .config import SchedulerSettings


def _parse_hhmm(value: str) -> int:
    hour, minute = (int(x) for x in value.split(":", 1))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"invalid HH:MM value: {value}")
    return hour * 60 + minute


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_scheduler_accounts(settings: SchedulerSettings) -> HHAccountsConfig:
    """Load both versioned TOMLs and validate every binding query-set reference."""

    discovery = load_discovery_config(settings.discovery_config)
    return load_accounts_config(settings.accounts_config, discovery=discovery)


def scheduler_timezone(
    settings: SchedulerSettings,
    accounts: HHAccountsConfig,
) -> str:
    """Prefer an explicit env override, otherwise use account config global timezone."""

    return settings.timezone or accounts.global_settings.timezone


def generate_times(
    *,
    target_date: date,
    timezone: ZoneInfo,
    runs: int,
    start_hhmm: str,
    end_hhmm: str,
    min_gap_minutes: int,
    rng: random.Random,
) -> list[datetime]:
    """Generate globally randomized times while preserving launch spacing."""

    if runs < 1:
        return []
    start_min = _parse_hhmm(start_hhmm)
    end_min = _parse_hhmm(end_hhmm)
    if end_min <= start_min:
        raise ValueError("window_end must be after window_start")
    window = end_min - start_min
    required = (runs - 1) * min_gap_minutes
    if required > window:
        raise ValueError("time window is too small for global slot count/min gap")
    slack = window - required
    offsets = sorted(rng.randint(0, slack) for _ in range(runs))
    minute_points = [start_min + offsets[i] + i * min_gap_minutes for i in range(runs)]
    result: list[datetime] = []
    for minutes in minute_points:
        hour, minute = divmod(minutes, 60)
        result.append(
            datetime.combine(target_date, time(hour=hour, minute=minute), tzinfo=timezone)
        )
    return result


def _runs_per_day(account: HHAccountConfig, runtime_mode: str) -> int:
    if runtime_mode == "observe":
        return account.observe_runs_per_day
    if runtime_mode == "apply":
        return account.apply_runs_per_day
    raise ValueError(f"unsupported scheduler runtime mode: {runtime_mode!r}")


def _interleaved_account_runs(
    accounts: HHAccountsConfig,
    runtime_mode: str,
) -> list[tuple[str, int]]:
    """Round-robin account runs so independent plans never collide."""

    enabled = accounts.enabled_accounts
    if not enabled:
        raise ValueError("HH account config has no enabled accounts")
    result: list[tuple[str, int]] = []
    max_runs = max(_runs_per_day(account, runtime_mode) for account in enabled)
    for run_number in range(1, max_runs + 1):
        for account in enabled:
            if run_number <= _runs_per_day(account, runtime_mode):
                result.append((account.key, run_number))
    return result


def _plan_account_metadata(accounts: HHAccountsConfig) -> list[dict[str, Any]]:
    """Return the config snapshot that makes a daily plan reusable safely."""

    return [
        {
            "account_key": account.key,
            "enabled": account.enabled,
            "observe_runs_per_day": account.observe_runs_per_day,
            "apply_runs_per_day": account.apply_runs_per_day,
            "apply_daily_cap": account.apply_daily_cap,
            "max_apply_per_run": account.max_apply_per_run,
        }
        for account in accounts.enabled_accounts
    ]


def generate_plan(
    *,
    target_date: date,
    settings: SchedulerSettings,
    accounts: HHAccountsConfig,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Generate schema-v3 account slots with one global spacing constraint."""

    settings.validate()
    rng = rng or random.SystemRandom()
    timezone_name = scheduler_timezone(settings, accounts)
    timezone = ZoneInfo(timezone_name)
    account_runs = _interleaved_account_runs(accounts, settings.runtime_mode.value)
    accounts_by_key = {account.key: account for account in accounts.enabled_accounts}
    times = generate_times(
        target_date=target_date,
        timezone=timezone,
        runs=len(account_runs),
        start_hhmm=settings.window_start,
        end_hhmm=settings.window_end,
        min_gap_minutes=settings.min_gap_minutes,
        rng=rng,
    )
    return {
        "event_type": "hh.scheduler.plan",
        "schema_version": 3,
        "runtime_mode": settings.runtime_mode.value,
        "date": target_date.isoformat(),
        "timezone": timezone_name,
        "global_min_gap_minutes": settings.min_gap_minutes,
        "generated_at": datetime.now(timezone).isoformat(),
        "accounts": _plan_account_metadata(accounts),
        "slots": [
            {
                "id": f"{account_key}-r{run_number:02d}",
                "account_key": account_key,
                "action": settings.runtime_mode.value,
                "run_number": run_number,
                "max_apply_per_run": (
                    accounts_by_key[account_key].max_apply_per_run
                    if settings.runtime_mode.value == "apply"
                    else None
                ),
                "scheduled_at": scheduled_at.isoformat(),
            }
            for (account_key, run_number), scheduled_at in zip(
                account_runs,
                times,
                strict=True,
            )
        ],
    }


def plan_path(settings: SchedulerSettings, target_date: date) -> Path:
    return settings.state_dir / f"plan-{target_date.isoformat()}.json"


async def _persist_plan_s3(plan: dict[str, Any]) -> str | None:
    try:
        from careerops_storage import S3JsonStore, S3Settings

        async with S3JsonStore(S3Settings.from_env()) as store:
            ref = await store.put_json(f"scheduler/date={plan['date']}/plan.json", plan)
        return ref.uri
    except Exception as exc:  # noqa: BLE001 - local scheduling survives S3 outage
        print(f"WARN: scheduler plan was not mirrored to S3: {exc}")
        return None


async def ensure_plan(
    settings: SchedulerSettings,
    *,
    target_date: date | None = None,
    force: bool = False,
    accounts: HHAccountsConfig | None = None,
) -> dict[str, Any]:
    """Return a compatible existing plan or create and mirror schema v3."""

    accounts = accounts or load_scheduler_accounts(settings)
    timezone = ZoneInfo(scheduler_timezone(settings, accounts))
    target_date = target_date or datetime.now(timezone).date()
    path = plan_path(settings, target_date)
    if path.exists() and not force:
        existing = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        if (
            existing.get("schema_version") == 3
            and existing.get("runtime_mode") == settings.runtime_mode.value
            and existing.get("accounts") == _plan_account_metadata(accounts)
        ):
            return existing

    plan = generate_plan(
        target_date=target_date,
        settings=settings,
        accounts=accounts,
    )
    _atomic_write(path, plan)
    uri = await _persist_plan_s3(plan)
    if uri:
        plan = {**plan, "s3_uri": uri}
        _atomic_write(path, plan)
    return plan


async def _async_main() -> None:
    parser = argparse.ArgumentParser(description="Generate the CareerOPS HH account plan")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to configured local date")
    args = parser.parse_args()
    settings = SchedulerSettings.from_env()
    accounts = load_scheduler_accounts(settings)
    target_date = date.fromisoformat(args.date) if args.date else None
    plan = await ensure_plan(
        settings,
        target_date=target_date,
        force=args.force,
        accounts=accounts,
    )
    print(json.dumps(plan, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
