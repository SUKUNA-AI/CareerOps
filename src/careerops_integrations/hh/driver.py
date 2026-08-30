from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


class HHDriverError(RuntimeError):
    pass


ParamScalar = str | int | bool
ParamValue = ParamScalar | list[ParamScalar] | tuple[ParamScalar, ...]


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
        method: str = "GET",
    ) -> dict[str, Any]:
        command = self._base_command() + ["call-api", endpoint]

        for key, value in (params or {}).items():
            values = value if isinstance(value, (list, tuple)) else (value,)
            for item in values:
                command.append(f"{key}={self._encode_param(item)}")

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

    def search_vacancies(
        self,
        *,
        text: str,
        area: int = 1,
        period: int = 14,
        order_by: str = "publication_time",
        per_page: int = 50,
        pages: int = 1,
        professional_roles: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        if not text.strip():
            raise ValueError("search text must not be empty")
        if not 1 <= per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")
        if pages < 1:
            raise ValueError("pages must be >= 1")

        found: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for page in range(pages):
            params: dict[str, ParamValue] = {
                "text": text,
                "area": area,
                "period": period,
                "order_by": order_by,
                "per_page": per_page,
                "page": page,
            }
            if professional_roles:
                params["professional_role"] = professional_roles

            payload = self.call_api("vacancies", params=params)
            items = payload.get("items") or []
            if not isinstance(items, list):
                raise HHDriverError("Unexpected HH vacancies response: items is not a list")

            for item in items:
                if not isinstance(item, dict) or "id" not in item:
                    continue
                vacancy_id = str(item["id"])
                if vacancy_id in seen_ids:
                    continue
                seen_ids.add(vacancy_id)
                found.append(item)

            total_pages = payload.get("pages")
            if isinstance(total_pages, int) and page + 1 >= total_pages:
                break

        return found

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
    def submit_application_with_test(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> dict[str, Any]:
        from .test_bridge import submit_vacancy_test_via_upstream

        return submit_vacancy_test_via_upstream(
            config_dir=self.config_dir,
            profile=self.profile,
            vacancy_id=vacancy_id,
            resume_id=resume_id,
            message=message,
        )

