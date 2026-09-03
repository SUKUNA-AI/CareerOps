from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import careerops_integrations.hh.batch_cli as batch_cli
import careerops_scheduler.dispatcher as dispatcher
from careerops_integrations.hh.runtime import RuntimeMode
from careerops_scheduler.config import SchedulerSettings


def test_account_quota_clamps_scheduler_and_per_run_limits() -> None:
    assert batch_cli._account_run_quota(
        configured_daily_cap=100,
        requested_max_responses=15,
        scheduler_remaining=7,
    ) == (7, 7)
    assert batch_cli._account_run_quota(
        configured_daily_cap=100,
        requested_max_responses=15,
        scheduler_remaining=None,
    ) == (100, 15)
    with pytest.raises(ValueError, match="must be >= 0"):
        batch_cli._account_run_quota(
            configured_daily_cap=100,
            requested_max_responses=15,
            scheduler_remaining=-1,
        )


def test_worker_command_is_explicit_and_never_appends_live_or_resume() -> None:
    settings = SchedulerSettings(repo_root=Path("/srv/careerops/app"))
    command = dispatcher._worker_command(
        settings,
        runtime_mode="observe",
        account_key="junior",
    )
    assert command[-4:] == ["--mode", "observe", "--account-key", "junior"]
    assert "--live" not in command
    assert "--resume-id" not in command
    assert "--profile" not in command


def test_apply_worker_receives_account_quota_without_static_resume() -> None:
    command = dispatcher._worker_command(
        SchedulerSettings(repo_root=Path("/srv/careerops/app")),
        runtime_mode="apply",
        account_key="junior",
        account_quota_remaining=37,
        max_apply_per_run=15,
    )
    assert command[-4:] == [
        "--account-quota-remaining",
        "37",
        "--max-responses",
        "15",
    ]
    assert "--live" not in command
    assert "--resume-id" not in command
    assert "--profile" not in command


def _plan() -> dict[str, Any]:
    return {
        "event_type": "hh.scheduler.plan",
        "schema_version": 3,
        "runtime_mode": "observe",
        "date": "2026-09-02",
        "timezone": "Europe/Moscow",
        "accounts": [
            {
                "account_key": "ml_3y",
                "enabled": True,
                "observe_runs_per_day": 1,
                "apply_runs_per_day": 2,
                "apply_daily_cap": 100,
                "max_apply_per_run": 50,
            },
            {
                "account_key": "junior",
                "enabled": True,
                "observe_runs_per_day": 1,
                "apply_runs_per_day": 2,
                "apply_daily_cap": 100,
                "max_apply_per_run": 50,
            },
        ],
        "slots": [
            {
                "id": "ml_3y-r01",
                "account_key": "ml_3y",
                "action": "observe",
                "run_number": 1,
                "max_apply_per_run": None,
                "scheduled_at": "2026-09-02T09:00:00+03:00",
            },
            {
                "id": "junior-r01",
                "account_key": "junior",
                "action": "observe",
                "run_number": 1,
                "max_apply_per_run": None,
                "scheduled_at": "2026-09-02T09:05:00+03:00",
            },
        ],
    }


def test_apply_state_is_regenerated_when_account_cap_changes(
    workspace_tmp_dir: Path,
) -> None:
    settings = SchedulerSettings(state_dir=workspace_tmp_dir)
    plan = _plan()
    plan["runtime_mode"] = "apply"
    plan["accounts"] = [plan["accounts"][0]]
    plan["slots"] = [plan["slots"][0]]
    plan["slots"][0]["action"] = "apply"
    plan["slots"][0]["max_apply_per_run"] = 50
    first = dispatcher._load_state(settings, plan)
    first["accounts"]["ml_3y"]["quota_consumed"] = 40
    first["accounts"]["ml_3y"]["quota_remaining"] = 60
    dispatcher._atomic_write(
        workspace_tmp_dir / "state-2026-09-02.json",
        first,
    )

    plan["accounts"][0]["apply_daily_cap"] = 3
    regenerated = dispatcher._load_state(settings, plan)
    assert regenerated["accounts"]["ml_3y"]["apply_daily_cap"] == 3
    assert regenerated["accounts"]["ml_3y"]["quota_consumed"] == 40
    assert regenerated["accounts"]["ml_3y"]["quota_remaining"] == 0


@pytest.mark.asyncio
async def test_captcha_pauses_only_affected_account_and_other_account_runs(
    workspace_tmp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SchedulerSettings(
        state_dir=workspace_tmp_dir,
        accounts_config=Path("config/hh_accounts.example.toml"),
        discovery_config=Path("config/hh_discovery.toml"),
        late_grace_minutes=180,
    )
    plan = _plan()

    async def fake_ensure_plan(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return plan

    calls: list[str] = []

    def fake_run_worker(
        settings: SchedulerSettings,
        *,
        runtime_mode: str,
        account_key: str,
    ):
        calls.append(account_key)
        return (
            0,
            [],
            {
                "account_key": account_key,
                "submitted": 0,
                "confirmed": 0,
                "stopped_on_captcha": account_key == "ml_3y",
                "summary_uri": f"s3://summary/{account_key}",
            },
        )

    async def fake_persist(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(dispatcher, "ensure_plan", fake_ensure_plan)
    monkeypatch.setattr(dispatcher, "_run_worker", fake_run_worker)
    monkeypatch.setattr(dispatcher, "_persist_dispatch_s3", fake_persist)
    now = datetime(2026, 9, 2, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    first = await dispatcher.dispatch_once(settings, now=now)
    second = await dispatcher.dispatch_once(settings, now=now)

    assert first["account_key"] == "ml_3y"
    assert first["account_paused"] is True
    assert second["account_key"] == "junior"
    assert second["account_paused"] is False
    assert calls == ["ml_3y", "junior"]

    state = json.loads(
        (workspace_tmp_dir / "state-2026-09-02.json").read_text(encoding="utf-8")
    )
    assert state["schema_version"] == 3
    assert state["accounts"]["ml_3y"]["paused"] is True
    assert state["accounts"]["ml_3y"]["pause_reason"] == "captcha_required"
    assert state["accounts"]["junior"]["paused"] is False
    assert state["accounts"]["junior"]["runs_completed"] == 1
    assert "carry" not in state
    assert "quota_remaining" not in state["accounts"]["junior"]
    assert "quota_consumed" not in state["accounts"]["junior"]


@pytest.mark.asyncio
async def test_apply_daily_quota_is_enforced_per_account_and_persisted(
    workspace_tmp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SchedulerSettings(
        runtime_mode=RuntimeMode.APPLY,
        state_dir=workspace_tmp_dir,
        accounts_config=Path("config/hh_accounts.example.toml"),
        discovery_config=Path("config/hh_discovery.toml"),
        late_grace_minutes=180,
    )
    plan = {
        "event_type": "hh.scheduler.plan",
        "schema_version": 3,
        "runtime_mode": "apply",
        "date": "2026-09-02",
        "timezone": "Europe/Moscow",
        "accounts": [
            {
                "account_key": "junior",
                "enabled": True,
                "observe_runs_per_day": 3,
                "apply_runs_per_day": 3,
                "apply_daily_cap": 3,
                "max_apply_per_run": 2,
            }
        ],
        "slots": [
            {
                "id": f"junior-r{index:02d}",
                "account_key": "junior",
                "action": "apply",
                "run_number": index,
                "max_apply_per_run": 2,
                "scheduled_at": f"2026-09-02T09:{index:02d}:00+03:00",
            }
            for index in range(1, 4)
        ],
    }

    async def fake_ensure_plan(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return plan

    quota_calls: list[int] = []

    def fake_run_worker(
        settings: SchedulerSettings,
        *,
        runtime_mode: str,
        account_key: str,
        account_quota_remaining: int | None = None,
        max_apply_per_run: int | None = None,
    ):
        assert runtime_mode == "apply"
        assert account_key == "junior"
        assert account_quota_remaining is not None
        assert max_apply_per_run == 2
        quota_calls.append(account_quota_remaining)
        consumed = min(2, account_quota_remaining)
        return (
            0,
            [],
            {
                "account_key": account_key,
                "submitted": consumed,
                "confirmed": consumed,
                "external_writes_attempted": consumed,
                "quota_consumed": consumed,
                "stopped_on_captcha": False,
                "summary_uri": f"s3://summary/{len(quota_calls)}",
            },
        )

    async def fake_persist(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(dispatcher, "ensure_plan", fake_ensure_plan)
    monkeypatch.setattr(dispatcher, "_run_worker", fake_run_worker)
    monkeypatch.setattr(dispatcher, "_persist_dispatch_s3", fake_persist)
    now = datetime(2026, 9, 2, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    first = await dispatcher.dispatch_once(settings, now=now)
    second = await dispatcher.dispatch_once(settings, now=now)
    third = await dispatcher.dispatch_once(settings, now=now)

    assert first["account_quota_remaining_before_run"] == 3
    assert first["account_quota_remaining_after_run"] == 1
    assert second["account_quota_remaining_before_run"] == 1
    assert second["account_quota_remaining_after_run"] == 0
    assert third == {"status": "nothing_due", "date": "2026-09-02"}
    assert quota_calls == [3, 1]

    state = json.loads(
        (workspace_tmp_dir / "state-2026-09-02.json").read_text(encoding="utf-8")
    )
    account_state = state["accounts"]["junior"]
    assert account_state["apply_daily_cap"] == 3
    assert account_state["quota_consumed"] == 3
    assert account_state["quota_remaining"] == 0
    assert account_state["slots"]["junior-r03"]["reason"] == "daily_cap_reached"
