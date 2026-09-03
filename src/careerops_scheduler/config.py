"""Validated non-secret settings for the multi-account HH scheduler."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from careerops_integrations.hh.configuration import (
    accounts_config_path_from_env,
    discovery_config_path_from_env,
)
from careerops_integrations.hh.runtime import RuntimeMode


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True, slots=True)
class SchedulerSettings:
    """Paths, global time window, and spacing for account-scoped slots."""

    runtime_mode: RuntimeMode = RuntimeMode.OBSERVE
    timezone: str | None = None
    window_start: str = "08:30"
    window_end: str = "23:00"
    min_gap_minutes: int = 30
    late_grace_minutes: int = 75
    state_dir: Path = Path("/var/lib/careerops/hh")
    repo_root: Path = Path("/srv/careerops/app")
    accounts_config: Path = Path("/etc/careerops/hh/accounts.toml")
    discovery_config: Path = Path("/srv/careerops/app/config/hh_discovery.toml")

    @classmethod
    def from_env(cls) -> SchedulerSettings:
        """Construct scheduler settings without loading credentials or resume IDs."""

        mode_value = os.getenv("CAREEROPS_HH_MODE")
        return cls(
            runtime_mode=RuntimeMode.parse(mode_value),
            timezone=os.getenv("CAREEROPS_HH_TIMEZONE") or None,
            window_start=os.getenv("CAREEROPS_HH_WINDOW_START", "08:30"),
            window_end=os.getenv("CAREEROPS_HH_WINDOW_END", "23:00"),
            min_gap_minutes=_env_int("CAREEROPS_HH_MIN_GAP_MINUTES", 30),
            late_grace_minutes=_env_int("CAREEROPS_HH_LATE_GRACE_MINUTES", 75),
            state_dir=Path(os.getenv("CAREEROPS_HH_STATE_DIR", "/var/lib/careerops/hh")),
            repo_root=Path(os.getenv("CAREEROPS_ROOT", "/srv/careerops/app")),
            accounts_config=accounts_config_path_from_env(),
            discovery_config=discovery_config_path_from_env(),
        )

    @property
    def compose_dir(self) -> Path:
        """Return the shared worker Compose directory."""

        return self.repo_root / "infra" / "compose" / "hh-worker"

    def validate(self) -> None:
        """Reject unsafe global timing values."""

        if self.min_gap_minutes < 0:
            raise ValueError("min_gap_minutes must be >= 0")
        if self.late_grace_minutes < 0:
            raise ValueError("late_grace_minutes must be >= 0")
