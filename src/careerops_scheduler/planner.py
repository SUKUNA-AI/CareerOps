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

from .config import SchedulerSettings


def _parse_hhmm(value: str) -> int:
    """Convert an HH:MM wall-clock value to minutes after midnight."""

    hour, minute = (int(x) for x in value.split(":", 1))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"invalid HH:MM value: {value}")
    return hour * 60 + minute


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Replace a local plan JSON file atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def generate_quotas(
    *,
    daily_cap: int,
    runs: int,
    max_per_run: int,
    min_per_run: int,
    rng: random.Random,
) -> list[int]:
    """Distribute the daily cap across runs within per-run limits."""

    if runs < 1 or daily_cap < 1:
        raise ValueError("runs and daily_cap must be positive")
    if daily_cap > runs * max_per_run:
        raise ValueError("daily cap does not fit into selected run count")

    floor = min(min_per_run, daily_cap // runs)
    floor = max(1, floor)
    quotas = [floor] * runs
    remaining = daily_cap - sum(quotas)

    while remaining > 0:
        candidates = [i for i, value in enumerate(quotas) if value < max_per_run]
        if not candidates:
            raise ValueError("could not distribute daily cap")
        idx = rng.choice(candidates)
        quotas[idx] += 1
        remaining -= 1

    rng.shuffle(quotas)
    return quotas


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
    """Generate randomized run times while preserving minimum gaps."""

    start_min = _parse_hhmm(start_hhmm)
    end_min = _parse_hhmm(end_hhmm)
    if end_min <= start_min:
        raise ValueError("window_end must be after window_start")

    window = end_min - start_min
    required = (runs - 1) * min_gap_minutes
    if required > window:
        raise ValueError("time window is too small for run count/min gap")

    # Transformation trick: sorted random slack + fixed minimum gaps.
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


def _select_run_count(settings: SchedulerSettings, rng: random.Random) -> int:
    """Choose a feasible configured run count for the daily cap."""

    feasible = [
        n
        for n in range(settings.min_runs, settings.max_runs + 1)
        if settings.daily_cap <= n * settings.max_per_run
    ]
    if not feasible:
        raise ValueError("no feasible run count for configured daily cap")
    return rng.choice(feasible)


def generate_plan(
    *,
    target_date: date,
    settings: SchedulerSettings,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Generate one complete scheduler plan for a local calendar day."""

    settings.validate()
    rng = rng or random.SystemRandom()
    tz = ZoneInfo(settings.timezone)
    runs = _select_run_count(settings, rng)
    quotas = generate_quotas(
        daily_cap=settings.daily_cap,
        runs=runs,
        max_per_run=settings.max_per_run,
        min_per_run=settings.min_per_run,
        rng=rng,
    )
    times = generate_times(
        target_date=target_date,
        timezone=tz,
        runs=runs,
        start_hhmm=settings.window_start,
        end_hhmm=settings.window_end,
        min_gap_minutes=settings.min_gap_minutes,
        rng=rng,
    )

    return {
        "event_type": "hh.scheduler.plan",
        "schema_version": 1,
        "date": target_date.isoformat(),
        "timezone": settings.timezone,
        "daily_cap": settings.daily_cap,
        "max_per_run": settings.max_per_run,
        "generated_at": datetime.now(tz).isoformat(),
        "slots": [
            {
                "id": f"r{i + 1:02d}",
                "scheduled_at": dt.isoformat(),
                "quota": quota,
            }
            for i, (dt, quota) in enumerate(zip(times, quotas, strict=True))
        ],
    }


def plan_path(settings: SchedulerSettings, target_date: date) -> Path:
    """Return the local JSON path for a daily plan."""

    return settings.state_dir / f"plan-{target_date.isoformat()}.json"


async def _persist_plan_s3(plan: dict[str, Any]) -> str | None:
    """Mirror a local scheduler plan through the asynchronous S3 API."""

    try:
        from careerops_storage import S3JsonStore, S3Settings

        async with S3JsonStore(S3Settings.from_env()) as store:
            ref = await store.put_json(f"scheduler/date={plan['date']}/plan.json", plan)
        return ref.uri
    except Exception as exc:  # noqa: BLE001 - local scheduling should survive S3 outage
        print(f"WARN: scheduler plan was not mirrored to S3: {exc}")
        return None


async def ensure_plan(
    settings: SchedulerSettings,
    *,
    target_date: date | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Return an existing daily plan or create and asynchronously mirror one."""

    tz = ZoneInfo(settings.timezone)
    target_date = target_date or datetime.now(tz).date()
    path = plan_path(settings, target_date)
    if path.exists() and not force:
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))

    plan = generate_plan(target_date=target_date, settings=settings)
    _atomic_write(path, plan)
    uri = await _persist_plan_s3(plan)
    if uri:
        plan = {**plan, "s3_uri": uri}
        _atomic_write(path, plan)
    return plan


async def _async_main() -> None:
    """Parse CLI arguments and ensure the requested scheduler plan."""

    parser = argparse.ArgumentParser(description="Generate the CareerOPS HH daily batch plan")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to today in configured timezone")
    args = parser.parse_args()

    settings = SchedulerSettings.from_env()
    settings.validate()
    target_date = date.fromisoformat(args.date) if args.date else None
    plan = await ensure_plan(settings, target_date=target_date, force=args.force)
    print(json.dumps(plan, ensure_ascii=False, indent=2))


def main() -> None:
    """Run the asynchronous planner from its synchronous console entry point."""

    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
