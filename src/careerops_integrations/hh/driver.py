"""Read-only subprocess wrapper around the pinned hh-applicant-tool CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


class HHDriverError(RuntimeError):
    """Report invalid output or failure from the upstream HH CLI."""


ParamScalar = str | int | bool
ParamValue = ParamScalar | Sequence[ParamScalar]


class HHApplicantToolCLI:
    """Thin read-only wrapper around the vendored HH transport.

    CareerOPS source ingestion may issue GET requests only. Application-side
    effects belong to the future application owner and must not leak into this
    adapter compatibility boundary.
    """

    def __init__(
        self,
        *,
        config_dir: str | Path,
        profile: str,
        python_executable: str | Path | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.config_dir = Path(config_dir).resolve()
        self.profile = profile.strip()
        if not self.profile:
            raise ValueError("profile must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self.python_executable = str(python_executable or sys.executable)
        self.timeout_seconds = float(timeout_seconds)

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

    @staticmethod
    def _encode_param(value: ParamScalar) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def call_api(
        self,
        endpoint: str,
        *,
        params: dict[str, ParamValue] | None = None,
    ) -> dict[str, Any]:
        """Issue exactly one GET through the upstream public call-api command."""

        normalized_endpoint = endpoint.strip().lstrip("/")
        if not normalized_endpoint:
            raise ValueError("endpoint must not be empty")

        command = self._base_command() + ["call-api", normalized_endpoint]
        for key, value in (params or {}).items():
            values = value if isinstance(value, (list, tuple)) else (value,)
            for item in values:
                command.append(f"{key}={self._encode_param(item)}")

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
