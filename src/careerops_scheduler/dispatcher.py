"""Dispatch one due account slot with account-scoped pause/failure state."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from .config import SchedulerSettings
from .planner import ensure_plan, load_scheduler_accounts, scheduler_timezone

SUMMARY_PREFIX = "CAREEROPS_SUMMARY_JSON="


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


@contextmanager
def _lock(state_dir: Path) -> Iterator[None]:
    """Hold the existing cross-platform single-dispatcher lock."""

    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "dispatcher.lock"
    with lock_path.open("a+") as handle:
        if sys.platform == "win32":
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write("\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _state_path(settings: SchedulerSettings, day: str) -> Path:
    return settings.state_dir / f"state-{day}.json"


def _new_state(plan: dict[str, Any]) -> dict[str, Any]:
    """Create schema-v3 mutable state grouped by account."""

    slots_by_account: dict[str, dict[str, dict[str, str]]] = {}
    for slot in plan["slots"]:
        slots_by_account.setdefault(slot["account_key"], {})[slot["id"]] = {
            "status": "pending"
        }
    account_states: dict[str, dict[str, Any]] = {}
    for account in plan["accounts"]:
        account_state: dict[str, Any] = {
            "enabled": bool(account["enabled"]),
            "paused": False,
            "pause_reason": None,
            "runs_completed": 0,
            "observe_runs_per_day": int(account["observe_runs_per_day"]),
            "apply_runs_per_day": int(account["apply_runs_per_day"]),
            "submitted": 0,
            "confirmed": 0,
            "external_writes_attempted": 0,
            "last_run_at": None,
            "last_success_at": None,
            "last_error": None,
            "slots": slots_by_account.get(account["account_key"], {}),
        }
        if plan["runtime_mode"] == "apply":
            account_state.update(
                {
                    "apply_daily_cap": int(account["apply_daily_cap"]),
                    "max_apply_per_run": int(account["max_apply_per_run"]),
                    "quota_consumed": 0,
                    "quota_remaining": int(account["apply_daily_cap"]),
                }
            )
        account_states[account["account_key"]] = account_state

    return {
        "event_type": "hh.scheduler.state",
        "schema_version": 3,
        "runtime_mode": plan["runtime_mode"],
        "date": plan["date"],
        "accounts": account_states,
    }


def _load_state(
    settings: SchedulerSettings,
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Read a compatible state or replace an obsolete schema-v1 day state."""

    path = _state_path(settings, plan["date"])
    existing: dict[str, Any] | None = None
    if path.exists():
        existing = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        existing_accounts = existing.get("accounts")
        account_states = (
            cast(dict[str, Any], existing_accounts)
            if isinstance(existing_accounts, dict)
            else {}
        )
        expected_account_keys = {
            account["account_key"] for account in plan["accounts"]
        }
        expected_slots = {
            account_key: {
                slot["id"]
                for slot in plan["slots"]
                if slot["account_key"] == account_key
            }
            for account_key in expected_account_keys
        }
        compatible = (
            existing.get("schema_version") == 3
            and existing.get("runtime_mode") == plan["runtime_mode"]
            and isinstance(existing_accounts, dict)
            and set(account_states) == expected_account_keys
            and all(
                isinstance(account_states.get(account_key), dict)
                for account_key in expected_account_keys
            )
        )
        if compatible:
            compatible = all(
                set(account_states[account_key].get("slots", {}))
                == expected_slots[account_key]
                for account_key in expected_account_keys
            )
        if compatible and plan["runtime_mode"] == "apply":
            caps_by_account = {
                account["account_key"]: int(account["apply_daily_cap"])
                for account in plan["accounts"]
            }
            compatible = all(
                {
                    "apply_daily_cap",
                    "max_apply_per_run",
                    "quota_consumed",
                    "quota_remaining",
                }.issubset(account_states[account_key])
                and int(account_states[account_key]["apply_daily_cap"])
                == caps_by_account[account_key]
                and int(account_states[account_key]["max_apply_per_run"])
                == next(
                    int(account["max_apply_per_run"])
                    for account in plan["accounts"]
                    if account["account_key"] == account_key
                )
                for account_key in expected_account_keys
            )
        if compatible:
            return existing
    state = _new_state(plan)
    if (
        existing is not None
        and existing.get("schema_version") in {2, 3}
        and existing.get("runtime_mode") == plan["runtime_mode"]
        and existing.get("date") == plan["date"]
        and isinstance(existing.get("accounts"), dict)
    ):
        # A same-day config/plan change must not erase already consumed APPLY
        # quota or account pause history. Merge only stable account/slot IDs.
        old_accounts = existing["accounts"]
        for account_key, account_state in state["accounts"].items():
            old_account = old_accounts.get(account_key)
            if not isinstance(old_account, dict):
                continue
            for key in (
                "paused",
                "pause_reason",
                "runs_completed",
                "submitted",
                "confirmed",
                "external_writes_attempted",
                "last_run_at",
                "last_success_at",
                "last_error",
            ):
                if key in old_account:
                    account_state[key] = old_account[key]
            old_slots = old_account.get("slots")
            if isinstance(old_slots, dict):
                for slot_id in set(account_state["slots"]).intersection(old_slots):
                    if isinstance(old_slots[slot_id], dict):
                        account_state["slots"][slot_id] = old_slots[slot_id]
            if plan["runtime_mode"] == "apply":
                consumed = int(
                    old_account.get(
                        "quota_consumed",
                        max(
                            int(old_account.get("submitted", 0)),
                            int(old_account.get("external_writes_attempted", 0)),
                        ),
                    )
                )
                account_state["quota_consumed"] = max(0, consumed)
                account_state["quota_remaining"] = max(
                    0,
                    int(account_state["apply_daily_cap"])
                    - int(account_state["quota_consumed"]),
                )
    _atomic_write(path, state)
    return state


def _extract_summary(lines: list[str]) -> dict[str, Any] | None:
    for line in reversed(lines):
        if line.startswith(SUMMARY_PREFIX):
            return cast(dict[str, Any], json.loads(line[len(SUMMARY_PREFIX) :]))
    return None


async def _persist_dispatch_s3(
    day: str,
    slot_id: str,
    payload: dict[str, Any],
) -> str | None:
    try:
        from careerops_storage import S3JsonStore, S3Settings

        async with S3JsonStore(S3Settings.from_env()) as store:
            ref = await store.put_json(
                f"scheduler/date={day}/slot={slot_id}/dispatch.json",
                payload,
            )
        return ref.uri
    except Exception as exc:  # noqa: BLE001 - local state remains authoritative
        print(f"WARN: dispatch state was not mirrored to S3: {exc}")
        return None


def _worker_command(
    settings: SchedulerSettings,
    *,
    runtime_mode: str,
    account_key: str,
    account_quota_remaining: int | None = None,
    max_apply_per_run: int | None = None,
) -> list[str]:
    """Build the production command without --live/profile/resume-id flags."""

    command = [
        "docker",
        "compose",
        "run",
        "--rm",
        "careerops-hh-worker",
        "python",
        "-m",
        "careerops_integrations.hh.batch_cli",
        "--mode",
        runtime_mode,
        "--account-key",
        account_key,
    ]
    if account_quota_remaining is not None:
        if runtime_mode != "apply":
            raise ValueError("application quota is valid only for APPLY workers")
        if account_quota_remaining < 0:
            raise ValueError("account_quota_remaining must be >= 0")
        command += [
            "--account-quota-remaining",
            str(account_quota_remaining),
        ]
        if max_apply_per_run is None or max_apply_per_run < 1:
            raise ValueError("APPLY workers require max_apply_per_run >= 1")
        command += ["--max-responses", str(max_apply_per_run)]
    elif max_apply_per_run is not None:
        raise ValueError("max_apply_per_run is valid only for APPLY workers")
    return command


def _run_worker(
    settings: SchedulerSettings,
    *,
    runtime_mode: str,
    account_key: str,
    account_quota_remaining: int | None = None,
    max_apply_per_run: int | None = None,
) -> tuple[int, list[str], dict[str, Any] | None]:
    """Run one shared image/account profile and capture its summary line."""

    command = _worker_command(
        settings,
        runtime_mode=runtime_mode,
        account_key=account_key,
        account_quota_remaining=account_quota_remaining,
        max_apply_per_run=max_apply_per_run,
    )
    process = subprocess.Popen(
        command,
        cwd=settings.compose_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: list[str] = []
    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.rstrip("\n")
        lines.append(line)
        print(line, flush=True)
    returncode = process.wait()
    return returncode, lines, _extract_summary(lines)


async def _finish_dispatch(
    *,
    plan: dict[str, Any],
    slot: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    uri = await _persist_dispatch_s3(plan["date"], slot["id"], payload)
    if uri:
        payload["s3_uri"] = uri
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


async def dispatch_once(
    settings: SchedulerSettings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one due unpaused account slot and leave other accounts serviceable."""

    settings.validate()
    accounts = load_scheduler_accounts(settings)
    timezone = ZoneInfo(scheduler_timezone(settings, accounts))
    now = now or datetime.now(timezone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone)
    else:
        now = now.astimezone(timezone)

    with _lock(settings.state_dir):
        plan = await ensure_plan(
            settings,
            target_date=now.date(),
            accounts=accounts,
        )
        state = _load_state(settings, plan)
        state_path = _state_path(settings, plan["date"])
        due_slot: dict[str, Any] | None = None

        for slot in plan["slots"]:
            account_state = state["accounts"][slot["account_key"]]
            slot_state = account_state["slots"].setdefault(
                slot["id"],
                {"status": "pending"},
            )
            if slot_state.get("status") != "pending":
                continue
            scheduled = datetime.fromisoformat(slot["scheduled_at"]).astimezone(timezone)
            if scheduled > now:
                break
            if account_state.get("paused") or not account_state.get("enabled", True):
                slot_state.update(
                    {
                        "status": "skipped",
                        "finished_at": now.isoformat(),
                        "reason": account_state.get("pause_reason") or "account_disabled",
                    }
                )
                continue
            if (
                plan["runtime_mode"] == "apply"
                and int(account_state.get("quota_remaining", 0)) <= 0
            ):
                slot_state.update(
                    {
                        "status": "skipped",
                        "finished_at": now.isoformat(),
                        "reason": "daily_cap_reached",
                    }
                )
                continue
            lateness_minutes = (now - scheduled).total_seconds() / 60
            if lateness_minutes > settings.late_grace_minutes:
                slot_state.update(
                    {
                        "status": "expired",
                        "finished_at": now.isoformat(),
                        "reason": "missed_time_window",
                    }
                )
                account_state["last_error"] = "missed_time_window"
                continue
            due_slot = slot
            break

        _atomic_write(state_path, state)
        if due_slot is None:
            result = {"status": "nothing_due", "date": plan["date"]}
            print(json.dumps(result, ensure_ascii=False))
            return result

        account_key = due_slot["account_key"]
        account_state = state["accounts"][account_key]
        slot_state = account_state["slots"][due_slot["id"]]
        slot_state.update({"status": "running", "started_at": now.isoformat()})
        account_state["last_run_at"] = now.isoformat()
        account_quota_remaining = (
            int(account_state["quota_remaining"])
            if plan["runtime_mode"] == "apply"
            else None
        )
        if account_quota_remaining is not None:
            slot_state["account_quota_remaining_before_run"] = (
                account_quota_remaining
            )
        _atomic_write(state_path, state)

        if account_quota_remaining is None:
            returncode, _lines, summary = _run_worker(
                settings,
                runtime_mode=plan["runtime_mode"],
                account_key=account_key,
            )
        else:
            returncode, _lines, summary = _run_worker(
                settings,
                runtime_mode=plan["runtime_mode"],
                account_key=account_key,
                account_quota_remaining=account_quota_remaining,
                max_apply_per_run=int(due_slot["max_apply_per_run"]),
            )
        finished_at = datetime.now(timezone)

        if (
            returncode != 0
            or summary is None
            or summary.get("account_key") != account_key
        ):
            reason = (
                "worker_summary_account_mismatch"
                if summary is not None and summary.get("account_key") != account_key
                else "worker_failed_without_summary"
            )
            slot_state.update(
                {
                    "status": "failed",
                    "finished_at": finished_at.isoformat(),
                    "returncode": returncode,
                    "reason": reason,
                }
            )
            account_state["paused"] = True
            account_state["pause_reason"] = reason
            account_state["last_error"] = reason
            _atomic_write(state_path, state)
            return await _finish_dispatch(
                plan=plan,
                slot=due_slot,
                payload={
                    "event_type": "hh.scheduler.dispatch",
                    "schema_version": 3,
                    "status": "failed",
                    "date": plan["date"],
                    "slot_id": due_slot["id"],
                    "account_key": account_key,
                    "runtime_mode": plan["runtime_mode"],
                    "reason": reason,
                    "account_paused": True,
                    "finished_at": finished_at.isoformat(),
                },
            )

        submitted = int(summary.get("submitted", 0))
        confirmed = int(summary.get("confirmed", 0))
        external_writes_attempted = int(summary.get("external_writes_attempted", 0))
        quota_consumed = int(
            summary.get(
                "quota_consumed",
                max(submitted, external_writes_attempted),
            )
        )
        quota_contract_limit = (
            min(
                account_quota_remaining,
                int(due_slot["max_apply_per_run"]),
            )
            if account_quota_remaining is not None
            else None
        )
        if quota_contract_limit is not None and not (
            0 <= quota_consumed <= quota_contract_limit
        ):
            reason = "worker_quota_contract_violation"
            slot_state.update(
                {
                    "status": "failed",
                    "finished_at": finished_at.isoformat(),
                    "returncode": returncode,
                    "reason": reason,
                }
            )
            account_state["paused"] = True
            account_state["pause_reason"] = reason
            account_state["last_error"] = reason
            _atomic_write(state_path, state)
            return await _finish_dispatch(
                plan=plan,
                slot=due_slot,
                payload={
                    "event_type": "hh.scheduler.dispatch",
                    "schema_version": 3,
                    "status": "failed",
                    "date": plan["date"],
                    "slot_id": due_slot["id"],
                    "account_key": account_key,
                    "runtime_mode": plan["runtime_mode"],
                    "reason": reason,
                    "reported_quota_consumed": quota_consumed,
                    "max_apply_per_run": due_slot["max_apply_per_run"],
                    "account_quota_remaining_before_run": account_quota_remaining,
                    "account_paused": True,
                    "finished_at": finished_at.isoformat(),
                },
            )
        stopped_on_captcha = bool(summary.get("stopped_on_captcha"))
        account_state["runs_completed"] = int(account_state["runs_completed"]) + 1
        account_state["submitted"] = int(account_state["submitted"]) + submitted
        account_state["confirmed"] = int(account_state["confirmed"]) + confirmed
        account_state["external_writes_attempted"] = int(
            account_state.get("external_writes_attempted", 0)
        ) + external_writes_attempted
        if account_quota_remaining is not None:
            account_state["quota_consumed"] = int(
                account_state["quota_consumed"]
            ) + quota_consumed
            account_state["quota_remaining"] = max(
                0,
                int(account_state["apply_daily_cap"])
                - int(account_state["quota_consumed"]),
            )
        account_state["last_success_at"] = finished_at.isoformat()
        account_state["last_error"] = None
        if stopped_on_captcha:
            account_state["paused"] = True
            account_state["pause_reason"] = "captcha_required"
            account_state["last_error"] = "captcha_required"
        slot_state.update(
            {
                "status": "completed",
                "finished_at": finished_at.isoformat(),
                "submitted": submitted,
                "confirmed": confirmed,
                "external_writes_attempted": external_writes_attempted,
                "summary_uri": summary.get("summary_uri"),
                "stopped_on_captcha": stopped_on_captcha,
            }
        )
        if account_quota_remaining is not None:
            slot_state["quota_consumed"] = quota_consumed
        _atomic_write(state_path, state)

        dispatch_payload: dict[str, Any] = {
            "event_type": "hh.scheduler.dispatch",
            "schema_version": 3,
            "status": "completed",
            "date": plan["date"],
            "slot_id": due_slot["id"],
            "account_key": account_key,
            "runtime_mode": plan["runtime_mode"],
            "submitted": submitted,
            "confirmed": confirmed,
            "external_writes_attempted": external_writes_attempted,
            "account_runs_completed": account_state["runs_completed"],
            "account_paused": account_state["paused"],
            "pause_reason": account_state["pause_reason"],
            "batch_summary_uri": summary.get("summary_uri"),
            "finished_at": finished_at.isoformat(),
        }
        if account_quota_remaining is not None:
            dispatch_payload.update(
                {
                    "apply_daily_cap": account_state["apply_daily_cap"],
                    "account_quota_remaining_before_run": account_quota_remaining,
                    "quota_consumed": quota_consumed,
                    "account_quota_remaining_after_run": account_state[
                        "quota_remaining"
                    ],
                }
            )
        return await _finish_dispatch(
            plan=plan,
            slot=due_slot,
            payload=dispatch_payload,
        )


async def _async_main() -> None:
    parser = argparse.ArgumentParser(description="Dispatch one due CareerOPS HH account slot")
    parser.parse_args()
    await dispatch_once(SchedulerSettings.from_env())


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
