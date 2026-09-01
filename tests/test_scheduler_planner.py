import random
from datetime import date
from zoneinfo import ZoneInfo

from careerops_scheduler.config import SchedulerSettings
from careerops_scheduler.planner import generate_plan


def test_plan_is_7_or_8_runs_and_sums_to_150():
    settings = SchedulerSettings()
    plan = generate_plan(
        target_date=date(2026, 8, 31),
        settings=settings,
        rng=random.Random(42),
    )
    quotas = [slot["quota"] for slot in plan["slots"]]
    assert len(quotas) in (7, 8)
    assert sum(quotas) == 150
    assert max(quotas) <= 25
    assert min(quotas) >= 1


def test_plan_respects_minimum_time_gap():
    settings = SchedulerSettings(min_gap_minutes=80)
    plan = generate_plan(
        target_date=date(2026, 8, 31),
        settings=settings,
        rng=random.Random(7),
    )
    from datetime import datetime

    times = [datetime.fromisoformat(slot["scheduled_at"]) for slot in plan["slots"]]
    gaps = [
        (b - a).total_seconds() / 60
        for a, b in zip(times, times[1:], strict=False)
    ]
    assert all(gap >= 80 for gap in gaps)
    assert all(t.tzinfo is not None for t in times)
    assert times[0].astimezone(ZoneInfo("Europe/Moscow")).date() == date(2026, 8, 31)
