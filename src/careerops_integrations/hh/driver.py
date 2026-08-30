from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


class HHDriverError(RuntimeError):
    pass


class HHApplicantToolCLI:
    """Thin wrapper around the hh-applicant-tool public CLI."""

    def __init__(
        self,
        *,
        config_dir: str | Path,
        profile: str,
        python_executable: str | Path | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.config_dir = Path(config_dir).resolve()
        self.profile = profile
        self.python_executable = str(python_executable or sys.executable)
        self.timeout_seconds = timeout_seconds

    def _base_command(self) -> list[str]:
        return [
            self.python_executable,
            "-m",
            "hh_applicant_tool",
            "--config-dir",
            str(self.config_dir),
            "--profile",
            self.profile,
        ]

    @staticmethod
    def _subprocess_env() -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env

    @staticmethod
    def _decode_json_output(stdout: str) -> dict[str, Any]:
        text = stdout.strip()
        if not text:
            raise HHDriverError("hh-applicant-tool returned empty stdout")

        try:
            value = json.loads(text)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass

        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value

        raise HHDriverError(
            "Could not parse JSON from hh-applicant-tool stdout. "
            f"First 500 chars: {text[:500]!r}"
        )

    def call_api(
        self,
        endpoint: str,
        *,
        params: dict[str, str | int | bool] | None = None,
        method: str = "GET",
    ) -> dict[str, Any]:
        command = self._base_command() + ["call-api", endpoint]

        for key, value in (params or {}).items():
            if isinstance(value, bool):
                encoded = "true" if value else "false"
            else:
                encoded = str(value)
            command.append(f"{key}={encoded}")

        if method.upper() != "GET":
            command += ["--method", method.upper()]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=self._subprocess_env(),
            timeout=self.timeout_seconds,
            check=False,
        )

        if result.returncode != 0:
            raise HHDriverError(
                "hh-applicant-tool call failed "
                f"(exit={result.returncode}). stderr={result.stderr[-1500:]!r}"
            )

        return self._decode_json_output(result.stdout)

    def fetch_vacancy(self, vacancy_id: str | int) -> dict[str, Any]:
        return self.call_api(f"vacancies/{vacancy_id}")

    def submit_application(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> dict[str, Any]:
        return self.call_api(
            "negotiations",
            params={
                "resume_id": resume_id,
                "vacancy_id": vacancy_id,
                "message": message,
            },
            method="POST",
        )
