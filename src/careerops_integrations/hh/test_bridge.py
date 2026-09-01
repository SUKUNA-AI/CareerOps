from __future__ import annotations

from pathlib import Path
from typing import Any, cast


def submit_vacancy_test_via_upstream(
    *,
    config_dir: str | Path,
    profile: str,
    vacancy_id: str,
    resume_id: str,
    message: str,
) -> dict[str, Any]:
    """Use vendored hh-applicant-tool's native private test-response flow.

    CareerOPS intentionally does not reimplement HH's web-form protocol here.
    The upstream source is vendored at a pinned revision and owns this behavior.
    """

    # Imports are local so ordinary API-only paths do not depend on upstream internals.
    from hh_applicant_tool.main import HHApplicantTool  # type: ignore[import-untyped]
    from hh_applicant_tool.operations.apply_vacancies import (  # type: ignore[import-untyped]
        Operation,
    )

    tool = HHApplicantTool()
    tool.config_dir = Path(config_dir).resolve()
    tool.profile_id = profile
    tool.api_delay = None
    tool.user_agent = None
    tool.proxy_url = None
    tool.openai_proxy_url = None
    tool.openai_timeout = None
    tool.openai_connect_timeout = None

    operation = Operation()
    operation.tool = tool
    operation.cover_letter_ai = None

    try:
        result = operation._solve_vacancy_test(
            vacancy_id=vacancy_id,
            resume_hash=resume_id,
            letter=message,
        )
    finally:
        try:
            tool.save_token()
        finally:
            try:
                tool.save_cookies()
            except Exception:
                pass

    success = result.get("success") if isinstance(result, dict) else None
    if success not in (True, "true", "True", 1, "1"):
        error = result.get("error") if isinstance(result, dict) else None
        raise RuntimeError(
            "upstream HH vacancy-test submission failed: "
            f"error={error!r}, result={result!r}"
        )

    return cast(dict[str, Any], result)
