from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    """Read an optional integer environment setting."""

    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True, slots=True)
class SchedulerSettings:
    """Validated operating limits and paths for the HH scheduler."""

    timezone: str = "Europe/Moscow"
    daily_cap: int = 150
    min_runs: int = 7
    max_runs: int = 8
    max_per_run: int = 25
    min_per_run: int = 14
    window_start: str = "08:30"
    window_end: str = "23:00"
    min_gap_minutes: int = 80
    late_grace_minutes: int = 75
    state_dir: Path = Path("/var/lib/careerops/hh")
    repo_root: Path = Path("/srv/careerops/app")
    resume_id: str = ""
    profile: str = "careerops-ml"
    area: int = 1
    period: int = 14
    pages: int = 3
    per_page: int = 100

    @classmethod
    def from_env(cls) -> SchedulerSettings:
        """Construct scheduler settings from environment variables."""

        return cls(
            timezone=os.getenv("CAREEROPS_HH_TIMEZONE", "Europe/Moscow"),
            daily_cap=_env_int("CAREEROPS_HH_DAILY_CAP", 150),
            min_runs=_env_int("CAREEROPS_HH_MIN_RUNS", 7),
            max_runs=_env_int("CAREEROPS_HH_MAX_RUNS", 8),
            max_per_run=_env_int("CAREEROPS_HH_MAX_PER_RUN", 25),
            min_per_run=_env_int("CAREEROPS_HH_MIN_PER_RUN", 14),
            window_start=os.getenv("CAREEROPS_HH_WINDOW_START", "08:30"),
            window_end=os.getenv("CAREEROPS_HH_WINDOW_END", "23:00"),
            min_gap_minutes=_env_int("CAREEROPS_HH_MIN_GAP_MINUTES", 80),
            late_grace_minutes=_env_int("CAREEROPS_HH_LATE_GRACE_MINUTES", 75),
            state_dir=Path(os.getenv("CAREEROPS_HH_STATE_DIR", "/var/lib/careerops/hh")),
            repo_root=Path(os.getenv("CAREEROPS_ROOT", "/srv/careerops/app")),
            resume_id=os.getenv("CAREEROPS_HH_RESUME_ID", ""),
            profile=os.getenv("CAREEROPS_HH_PROFILE", "careerops-ml"),
            area=_env_int("CAREEROPS_HH_AREA", 1),
            period=_env_int("CAREEROPS_HH_PERIOD", 14),
            pages=_env_int("CAREEROPS_HH_PAGES", 3),
            per_page=_env_int("CAREEROPS_HH_PER_PAGE", 100),
        )

    @property
    def compose_dir(self) -> Path:
        """Return the hh-worker Docker Compose directory."""

        return self.repo_root / "infra" / "compose" / "hh-worker"

    def validate(self) -> None:
        """Reject impossible or unsafe scheduler limits."""

        if self.daily_cap < 1:
            raise ValueError("daily_cap must be >= 1")
        if not 1 <= self.min_runs <= self.max_runs:
            raise ValueError("invalid run count range")
        if not 1 <= self.max_per_run <= 25:
            raise ValueError("max_per_run must be between 1 and 25")
        if self.daily_cap > self.max_runs * self.max_per_run:
            raise ValueError("daily_cap cannot fit into max_runs * max_per_run")
        if self.daily_cap > self.min_runs * self.max_per_run and self.min_runs == self.max_runs:
            raise ValueError("daily_cap cannot fit into configured number of runs")
        if self.per_page < 1 or self.per_page > 100:
            raise ValueError("per_page must be between 1 and 100")
        if self.pages < 1:
            raise ValueError("pages must be >= 1")
