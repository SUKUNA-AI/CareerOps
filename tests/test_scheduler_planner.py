import random
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from careerops_integrations.hh.runtime import RuntimeMode
from careerops_scheduler.config import SchedulerSettings
from careerops_scheduler.planner import generate_plan, load_scheduler_accounts


def _settings(**overrides):
    values = {
        "accounts_config": Path("config/hh_accounts.example.toml"),
        "discovery_config": Path("config/hh_discovery.toml"),
    }
    values.update(overrides)
    return SchedulerSettings(**values)


def test_plan_v3_contains_all_accounts_and_configured_observe_runs() -> None:
    settings = _settings()
    plan = generate_plan(
        target_date=date(2026, 9, 2),
        settings=settings,
        accounts=load_scheduler_accounts(settings),
        rng=random.Random(42),
    )
    assert plan["schema_version"] == 3
    assert plan["runtime_mode"] == "observe"
    assert "daily_cap" not in plan
    assert all("quota" not in slot for slot in plan["slots"])
    counts = Counter(slot["account_key"] for slot in plan["slots"])
    assert counts == {"ml_3y": 3, "ml_5y": 3, "junior": 3}
    assert [slot["account_key"] for slot in plan["slots"][:6]] == [
        "ml_3y",
        "ml_5y",
        "junior",
        "ml_3y",
        "ml_5y",
        "junior",
    ]
    assert {account["apply_daily_cap"] for account in plan["accounts"]} == {100}
    assert {account["apply_runs_per_day"] for account in plan["accounts"]} == {7}
    assert {account["max_apply_per_run"] for account in plan["accounts"]} == {15}
    assert all(slot["max_apply_per_run"] is None for slot in plan["slots"])


def test_apply_plan_has_distinct_cadence_and_can_reach_daily_cap() -> None:
    settings = _settings(runtime_mode=RuntimeMode.APPLY)
    plan = generate_plan(
        target_date=date(2026, 9, 2),
        settings=settings,
        accounts=load_scheduler_accounts(settings),
        rng=random.Random(42),
    )

    counts = Counter(slot["account_key"] for slot in plan["slots"])
    assert counts == {"ml_3y": 7, "ml_5y": 7, "junior": 7}
    assert {slot["max_apply_per_run"] for slot in plan["slots"]} == {15}
    for account in plan["accounts"]:
        assert (
            account["apply_runs_per_day"] * account["max_apply_per_run"]
            >= account["apply_daily_cap"]
        )


def test_plan_respects_one_global_minimum_gap() -> None:
    settings = _settings(min_gap_minutes=80)
    plan = generate_plan(
        target_date=date(2026, 9, 2),
        settings=settings,
        accounts=load_scheduler_accounts(settings),
        rng=random.Random(7),
    )
    times = [datetime.fromisoformat(slot["scheduled_at"]) for slot in plan["slots"]]
    gaps = [
        (later - earlier).total_seconds() / 60
        for earlier, later in zip(times, times[1:], strict=False)
    ]
    assert all(gap >= 80 for gap in gaps)
    assert all(value.tzinfo is not None for value in times)
    assert times[0].astimezone(ZoneInfo("Europe/Moscow")).date() == date(2026, 9, 2)


def test_slot_ids_and_actions_are_account_scoped() -> None:
    settings = _settings()
    plan = generate_plan(
        target_date=date(2026, 9, 2),
        settings=settings,
        accounts=load_scheduler_accounts(settings),
        rng=random.Random(1),
    )
    assert len({slot["id"] for slot in plan["slots"]}) == len(plan["slots"])
    assert all(slot["id"].startswith(slot["account_key"] + "-r") for slot in plan["slots"])
    assert {slot["action"] for slot in plan["slots"]} == {"observe"}
