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
from .planner import ensure_plan

SUMMARY_PREFIX = "CAREEROPS_SUMMARY_JSON="


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Replace a local JSON state file atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def _lock(state_dir: Path) -> Iterator[None]:
    """Hold the cross-platform single-dispatcher file lock."""

    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "dispatcher.lock"
    with lock_path.open("a+") as fh:
        if sys.platform == "win32":
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                fh.write("\0")
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if sys.platform == "win32":
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


def _state_path(settings: SchedulerSettings, day: str) -> Path:
    """Return the local state-file path for one scheduler day."""

    return settings.state_dir / f"state-{day}.json"


def _new_state(plan: dict[str, Any]) -> dict[str, Any]:
    """Create initial mutable state for a generated plan."""

    return {
        "event_type": "hh.scheduler.state",
        "schema_version": 1,
        "date": plan["date"],
        "submitted": 0,
        "confirmed": 0,
        "carry": 0,
        "paused": False,
        "pause_reason": None,
        "slots": {slot["id"]: {"status": "pending"} for slot in plan["slots"]},
    }


def _load_state(
    settings: SchedulerSettings,
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Read existing day state or create it from the plan."""

    path = _state_path(settings, plan["date"])
    if path.exists():
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    state = _new_state(plan)
    _atomic_write(path, state)
    return state


def effective_quota(*, planned: int, carry: int, remaining: int, max_per_run: int) -> int:
    """Clamp planned quota plus carry to run and daily limits."""

    return max(0, min(max_per_run, remaining, planned + max(0, carry)))


def _extract_summary(lines: list[str]) -> dict[str, Any] | None:
    """Extract the worker's last machine-readable batch summary."""

    for line in reversed(lines):
        if line.startswith(SUMMARY_PREFIX):
            return cast(dict[str, Any], json.loads(line[len(SUMMARY_PREFIX) :]))
    return None


async def _persist_dispatch_s3(
    day: str,
    slot_id: str,
    payload: dict[str, Any],
) -> str | None:
    """Mirror one dispatch result through the asynchronous S3 API."""

    try:
        from careerops_storage import S3JsonStore, S3Settings

        async with S3JsonStore(S3Settings.from_env()) as store:
            ref = await store.put_json(
                f"scheduler/date={day}/slot={slot_id}/dispatch.json",
                payload,
            )
        return ref.uri
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: dispatch state was not mirrored to S3: {exc}")
        return None


def _run_worker(
    settings: SchedulerSettings,
    quota: int,
) -> tuple[int, list[str], dict[str, Any] | None]:
    """Run the existing container worker and capture its summary line."""

    if not settings.resume_id:
        raise RuntimeError("CAREEROPS_HH_RESUME_ID is not configured")

    command = [
        "docker",
        "compose",
        "run",
        "--rm",
        "careerops-hh-worker",
        "python",
        "-m",
        "careerops_integrations.hh.batch_cli",
        "--profile",
        settings.profile,
        "--resume-id",
        settings.resume_id,
        "--area",
        str(settings.area),
        "--period",
        str(settings.period),
        "--pages",
        str(settings.pages),
        "--per-page",
        str(settings.per_page),
        "--max-responses",
        str(quota),
        "--live",
    ]

    proc = subprocess.Popen(
        command,
        cwd=settings.compose_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        lines.append(line)
        print(line, flush=True)
    returncode = proc.wait()
    return returncode, lines, _extract_summary(lines)


async def dispatch_once(
    settings: SchedulerSettings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one due scheduler slot and asynchronously mirror its result."""

    settings.validate()
    tz = ZoneInfo(settings.timezone)
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    with _lock(settings.state_dir):
        plan = await ensure_plan(settings, target_date=now.date())
        state = _load_state(settings, plan)
        state_path = _state_path(settings, plan["date"])

        if state.get("paused"):
            result = {"status": "paused", "reason": state.get("pause_reason")}
            print(json.dumps(result, ensure_ascii=False))
            return result

        remaining = settings.daily_cap - int(state.get("submitted", 0))
        if remaining <= 0:
            result = {"status": "daily_cap_reached", "submitted": state.get("submitted", 0)}
            print(json.dumps(result, ensure_ascii=False))
            return result

        due_slot: dict[str, Any] | None = None
        for slot in plan["slots"]:
            slot_state = state["slots"].setdefault(slot["id"], {"status": "pending"})
            if slot_state.get("status") != "pending":
                continue
            scheduled = datetime.fromisoformat(slot["scheduled_at"]).astimezone(tz)
            if scheduled > now:
                break
            lateness_minutes = (now - scheduled).total_seconds() / 60
            if lateness_minutes > settings.late_grace_minutes:
                slot_state.update(
                    {
                        "status": "expired",
                        "expired_at": now.isoformat(),
                        "reason": "missed_time_window",
                    }
                )
                state["carry"] = int(state.get("carry", 0)) + int(slot["quota"])
                continue
            due_slot = slot
            break

        _atomic_write(state_path, state)

        if due_slot is None:
            result = {"status": "nothing_due", "date": plan["date"]}
            print(json.dumps(result, ensure_ascii=False))
            return result

        slot_id = due_slot["id"]
        slot_state = state["slots"][slot_id]
        remaining = settings.daily_cap - int(state.get("submitted", 0))
        quota = effective_quota(
            planned=int(due_slot["quota"]),
            carry=int(state.get("carry", 0)),
            remaining=remaining,
            max_per_run=settings.max_per_run,
        )
        if quota <= 0:
            return {"status": "daily_cap_reached"}

        carry_used = max(0, quota - int(due_slot["quota"]))
        state["carry"] = max(0, int(state.get("carry", 0)) - carry_used)
        slot_state.update(
            {
                "status": "running",
                "started_at": now.isoformat(),
                "planned_quota": int(due_slot["quota"]),
                "effective_quota": quota,
            }
        )
        _atomic_write(state_path, state)

        returncode, lines, summary = _run_worker(settings, quota)
        finished_at = datetime.now(tz)

        if returncode != 0 or summary is None:
            slot_state.update(
                {
                    "status": "failed",
                    "finished_at": finished_at.isoformat(),
                    "returncode": returncode,
                    "reason": "worker_failed_without_summary",
                }
            )
            state["paused"] = True
            state["pause_reason"] = "worker_failed_without_summary"
            _atomic_write(state_path, state)
            raise RuntimeError("HH worker failed without a machine-readable summary")

        submitted = int(summary.get("submitted", 0))
        confirmed = int(summary.get("confirmed", 0))
        state["submitted"] = int(state.get("submitted", 0)) + submitted
        state["confirmed"] = int(state.get("confirmed", 0)) + confirmed

        shortfall = max(0, quota - submitted)
        if not summary.get("stopped_on_captcha"):
            state["carry"] = min(settings.daily_cap, int(state.get("carry", 0)) + shortfall)

        slot_state.update(
            {
                "status": "completed",
                "finished_at": finished_at.isoformat(),
                "submitted": submitted,
                "confirmed": confirmed,
                "summary_uri": summary.get("summary_uri"),
                "stopped_on_captcha": bool(summary.get("stopped_on_captcha")),
            }
        )

        if summary.get("stopped_on_captcha"):
            state["paused"] = True
            state["pause_reason"] = "captcha_required"

        _atomic_write(state_path, state)

        dispatch_payload = {
            "event_type": "hh.scheduler.dispatch",
            "schema_version": 1,
            "date": plan["date"],
            "slot_id": slot_id,
            "planned_quota": int(due_slot["quota"]),
            "effective_quota": quota,
            "submitted": submitted,
            "confirmed": confirmed,
            "daily_submitted": state["submitted"],
            "daily_confirmed": state["confirmed"],
            "carry": state["carry"],
            "paused": state["paused"],
            "pause_reason": state["pause_reason"],
            "batch_summary_uri": summary.get("summary_uri"),
            "finished_at": finished_at.isoformat(),
        }
        uri = await _persist_dispatch_s3(plan["date"], slot_id, dispatch_payload)
        if uri:
            dispatch_payload["s3_uri"] = uri
        print(json.dumps(dispatch_payload, ensure_ascii=False, indent=2))
        return dispatch_payload


async def _async_main() -> None:
    """Parse dispatcher CLI arguments and run one due slot."""

    parser = argparse.ArgumentParser(description="Dispatch one due CareerOPS HH batch")
    parser.parse_args()
    settings = SchedulerSettings.from_env()
    await dispatch_once(settings)


def main() -> None:
    """Run the asynchronous dispatcher from its synchronous console entry point."""

    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
