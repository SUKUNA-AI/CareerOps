from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime import HHExternalWriteGuard


class HHDriverError(RuntimeError):
    """Report invalid output or failure from the upstream HH CLI."""


ParamScalar = str | int | bool
ParamValue = ParamScalar | Sequence[ParamScalar]


@dataclass(frozen=True, slots=True)
class HHVacancySearchPage:
    """One exact HH vacancy-search response and its zero-based page number."""

    page: int
    payload: dict[str, Any]


class HHApplicantToolCLI:
    """Thin wrapper around the hh-applicant-tool public CLI."""

    def __init__(
        self,
        *,
        config_dir: str | Path,
        profile: str,
        python_executable: str | Path | None = None,
        timeout_seconds: float = 60.0,
        external_write_guard: HHExternalWriteGuard | None = None,
    ) -> None:
        """Configure the upstream CLI process and timeout."""

        self.config_dir = Path(config_dir).resolve()
        self.profile = profile
        self.python_executable = str(python_executable or sys.executable)
        self.timeout_seconds = timeout_seconds
        self.external_write_guard = external_write_guard or HHExternalWriteGuard()

    def _base_command(self) -> list[str]:
        """Build common arguments for every upstream CLI invocation."""

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
        """Force stable UTF-8 encoding for the child process."""

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env

    @staticmethod
    def _decode_json_output(stdout: str) -> dict[str, Any]:
        """Decode a JSON object from plain or log-prefixed CLI output."""

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
        """Encode one API query parameter for the upstream CLI."""

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
        """Invoke the upstream public call-api command and validate JSON output."""

        normalized_method = method.upper()
        if normalized_method != "GET":
            self.external_write_guard.require(
                f"HH {normalized_method} {endpoint}"
            )

        command = self._base_command() + ["call-api", endpoint]

        for key, value in (params or {}).items():
            values = value if isinstance(value, (list, tuple)) else (value,)
            for item in values:
                command.append(f"{key}={self._encode_param(item)}")

        if normalized_method != "GET":
            command += ["--method", normalized_method]

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
        """Search paginated vacancies and deduplicate them by HH id."""

        search_pages = self.search_vacancy_pages(
            text=text,
            area=area,
            period=period,
            order_by=order_by,
            per_page=per_page,
            pages=pages,
            professional_roles=professional_roles,
        )

        found: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for search_page in search_pages:
            items = search_page.payload.get("items")
            if items is None:
                items = []
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
        return found

    def search_vacancy_pages(
        self,
        *,
        text: str,
        area: int = 1,
        period: int = 14,
        order_by: str = "publication_time",
        per_page: int = 50,
        pages: int = 1,
        professional_roles: list[int] | None = None,
    ) -> Iterator[HHVacancySearchPage]:
        """Yield exact ordered HH search pages before flattening or deduplication."""

        if not text.strip():
            raise ValueError("search text must not be empty")
        if not 1 <= per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")
        if pages < 1:
            raise ValueError("pages must be >= 1")

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
            yield HHVacancySearchPage(page=page, payload=payload)

            total_pages = payload.get("pages")
            if isinstance(total_pages, int) and page + 1 >= total_pages:
                break

    def fetch_vacancy(self, vacancy_id: str | int) -> dict[str, Any]:
        """Fetch one full HH vacancy payload."""

        return self.call_api(f"vacancies/{vacancy_id}")

    def fetch_resume(self, resume_id: str) -> dict[str, Any]:
        """Fetch one HH resume payload for factual cover-letter matching."""

        return self.call_api(f"resumes/{resume_id}")

    def list_resumes(self) -> list[dict[str, Any]]:
        """Return the authoritative current resume inventory for this profile."""

        resumes: list[dict[str, Any]] = []
        page = 0
        while True:
            payload = self.call_api(
                "resumes/mine",
                params={"page": page, "per_page": 100},
            )
            items = payload.get("items")
            if not isinstance(items, list):
                raise HHDriverError(
                    "Unexpected HH resumes/mine response: items is not a list"
                )
            for item in items:
                if not isinstance(item, dict):
                    raise HHDriverError(
                        "Unexpected HH resumes/mine response: "
                        "resume item is not an object"
                    )
                resumes.append(item)

            pages = payload.get("pages")
            if not isinstance(pages, int) or pages < 0:
                raise HHDriverError(
                    "Unexpected HH resumes/mine response: pages is not a "
                    "non-negative integer"
                )
            if page + 1 >= pages:
                break
            page += 1
        return resumes

    def find_application_evidence(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
    ) -> dict[str, Any]:
        """Read resume-specific negotiation evidence through the upstream CLI."""

        matches: list[dict[str, str | None]] = []
        page = 0
        while True:
            payload = self.call_api(
                "negotiations",
                params={
                    "vacancy_id": vacancy_id,
                    "page": page,
                    "per_page": 100,
                },
            )
            items = payload.get("items")
            if not isinstance(items, list):
                raise HHDriverError(
                    "Unexpected HH negotiations response: items is not a list"
                )
            for item in items:
                if not isinstance(item, dict):
                    raise HHDriverError(
                        "Unexpected HH negotiations response: item is not an object"
                    )
                item_resume = item.get("resume")
                item_vacancy = item.get("vacancy")
                if not isinstance(item_resume, dict) or not isinstance(item_vacancy, dict):
                    raise HHDriverError(
                        "HH negotiation item lacks resume/vacancy identity"
                    )
                item_resume_id = str(item_resume.get("id") or "").strip()
                item_vacancy_id = str(item_vacancy.get("id") or "").strip()
                if not item_resume_id or not item_vacancy_id:
                    raise HHDriverError(
                        "HH negotiation item has empty resume/vacancy identity"
                    )
                if item_resume_id != resume_id or item_vacancy_id != vacancy_id:
                    continue
                state = item.get("state")
                state_id = (
                    str(state.get("id"))
                    if isinstance(state, dict) and state.get("id") is not None
                    else None
                )
                matches.append(
                    {
                        "negotiation_id": str(item.get("id") or "") or None,
                        "state": state_id,
                    }
                )

            pages = payload.get("pages")
            if not isinstance(pages, int) or pages < 0:
                raise HHDriverError(
                    "Unexpected HH negotiations response: pages is not a "
                    "non-negative integer"
                )
            if page + 1 >= pages:
                break
            page += 1

        return {
            "source": "hh_negotiations",
            "source_profile": self.profile,
            "source_resume_id": resume_id,
            "vacancy_id": vacancy_id,
            "found": bool(matches),
            "matches": matches,
        }

    def submit_application(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> dict[str, Any]:
        """Submit a standard HH negotiation application."""

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
        """Delegate a test-bearing vacancy to the existing upstream bridge."""

        self.external_write_guard.require("HH vacancy-test submission")

        from .test_bridge import submit_vacancy_test_via_upstream

        return submit_vacancy_test_via_upstream(
            config_dir=self.config_dir,
            profile=self.profile,
            vacancy_id=vacancy_id,
            resume_id=resume_id,
            message=message,
            external_write_guard=self.external_write_guard,
        )

