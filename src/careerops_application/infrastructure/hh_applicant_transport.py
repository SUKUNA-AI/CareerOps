from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import requests
from hh_applicant_tool.api import errors as hh_errors
from hh_applicant_tool.application_transport import ApplicantTransport as VendorApplicantTransport
from hh_applicant_tool.application_transport import (
    QuestionnaireRequired,
    SubmissionOutcomeUnknown,
)
from hh_applicant_tool.main import HHApplicantTool

from ..domain import (
    QuestionnaireQuestion,
    TransportPrecheck,
    TransportPrecheckState,
    TransportSubmitResult,
    TransportSubmitState,
)


class HHApplicantTransport:
    """The only CareerOPS facade allowed to depend on vendored applicant mechanics."""

    def __init__(
        self,
        *,
        config_dir: Path,
        request_timeout_seconds: float = 45.0,
        operation_timeout_seconds: float = 90.0,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be > 0")
        if operation_timeout_seconds <= 0:
            raise ValueError("operation_timeout_seconds must be > 0")
        if request_timeout_seconds > operation_timeout_seconds:
            raise ValueError("request timeout must not exceed operation timeout")
        self._config_dir = config_dir
        self._request_timeout_seconds = request_timeout_seconds
        self._operation_timeout_seconds = operation_timeout_seconds

    async def precheck(
        self,
        *,
        account_key: str,
        vacancy_id: str,
        resume_id: str,
        cover_letter: str,
    ) -> TransportPrecheck:
        return await asyncio.to_thread(
            self._precheck_sync,
            account_key,
            vacancy_id,
            resume_id,
            cover_letter,
        )

    async def submit(
        self,
        *,
        account_key: str,
        vacancy_id: str,
        resume_id: str,
        cover_letter: str,
        questionnaire_answers: Mapping[str, str],
    ) -> TransportSubmitResult:
        return await asyncio.to_thread(
            self._submit_sync,
            account_key,
            vacancy_id,
            resume_id,
            cover_letter,
            questionnaire_answers,
        )

    async def find_submission(
        self,
        *,
        account_key: str,
        vacancy_id: str,
        resume_id: str,
    ) -> str | None:
        return await asyncio.to_thread(
            self._find_submission_sync,
            account_key,
            vacancy_id,
            resume_id,
        )

    def _precheck_sync(
        self,
        account_key: str,
        vacancy_id: str,
        resume_id: str,
        cover_letter: str,
    ) -> TransportPrecheck:
        tool = self._tool(account_key, deadline=time.monotonic() + self._operation_timeout_seconds)
        try:
            result = VendorApplicantTransport(tool).precheck(
                vacancy_id=vacancy_id,
                resume_id=resume_id,
                cover_letter=cover_letter,
            )
            if result.already_submitted:
                return TransportPrecheck(
                    state=TransportPrecheckState.ALREADY_SUBMITTED,
                    reason_code="application.already_submitted",
                    upstream_id=result.negotiation_id,
                )
            if not result.ready:
                questions = tuple(
                    QuestionnaireQuestion(
                        question_id=item.question_id,
                        text=item.text,
                        options=item.options,
                    )
                    for item in result.questions
                )
                return TransportPrecheck(
                    state=TransportPrecheckState.BLOCKED,
                    reason_code=f"application.{result.reason or 'precheck_blocked'}",
                    questions=questions,
                )
            return TransportPrecheck(state=TransportPrecheckState.READY)
        except hh_errors.LimitExceeded:
            return TransportPrecheck(
                state=TransportPrecheckState.SAFE_FAILURE,
                reason_code="application.hh_limit_exceeded",
            )
        except (hh_errors.CaptchaRequired, hh_errors.Forbidden):
            return TransportPrecheck(
                state=TransportPrecheckState.BLOCKED,
                reason_code="application.hh_auth_required",
            )
        except (hh_errors.BadResponse, requests.RequestException):
            return TransportPrecheck(
                state=TransportPrecheckState.SAFE_FAILURE,
                reason_code="application.hh_transport_unavailable",
            )
        finally:
            self._persist(tool)

    def _submit_sync(
        self,
        account_key: str,
        vacancy_id: str,
        resume_id: str,
        cover_letter: str,
        questionnaire_answers: Mapping[str, str],
    ) -> TransportSubmitResult:
        tool = self._tool(account_key, deadline=time.monotonic() + self._operation_timeout_seconds)
        try:
            result = VendorApplicantTransport(tool).submit(
                vacancy_id=vacancy_id,
                resume_id=resume_id,
                cover_letter=cover_letter,
                questionnaire_answers=questionnaire_answers,
            )
            return TransportSubmitResult(
                state=TransportSubmitState.SUBMITTED,
                upstream_id=result.negotiation_id,
            )
        except QuestionnaireRequired:
            return TransportSubmitResult(
                state=TransportSubmitState.BLOCKED,
                reason_code="application.questionnaire_required",
            )
        except hh_errors.LimitExceeded:
            return TransportSubmitResult(
                state=TransportSubmitState.SAFE_FAILURE,
                reason_code="application.hh_limit_exceeded",
            )
        except hh_errors.CaptchaRequired:
            return TransportSubmitResult(
                state=TransportSubmitState.BLOCKED,
                reason_code="application.hh_captcha_required",
            )
        except hh_errors.Forbidden:
            return TransportSubmitResult(
                state=TransportSubmitState.BLOCKED,
                reason_code="application.hh_auth_required",
            )
        except hh_errors.Redirect:
            return TransportSubmitResult(
                state=TransportSubmitState.BLOCKED,
                reason_code="application.hh_response_form_required",
            )
        except hh_errors.BadRequest as exc:
            if hh_errors.ApiError.has_error_value("already_applied", exc.data):
                return TransportSubmitResult(
                    state=TransportSubmitState.ALREADY_SUBMITTED,
                    reason_code="application.already_submitted",
                )
            return TransportSubmitResult(
                state=TransportSubmitState.BLOCKED,
                reason_code="application.hh_rejected_request",
            )
        except (
            hh_errors.InternalServerError,
            hh_errors.BadResponse,
            SubmissionOutcomeUnknown,
            requests.RequestException,
        ):
            return TransportSubmitResult(
                state=TransportSubmitState.UNCERTAIN,
                reason_code="application.submit_outcome_unknown",
            )
        finally:
            self._persist(tool)

    def _find_submission_sync(
        self,
        account_key: str,
        vacancy_id: str,
        resume_id: str,
    ) -> str | None:
        tool = self._tool(account_key, deadline=time.monotonic() + self._operation_timeout_seconds)
        try:
            negotiation_id = VendorApplicantTransport(tool).find_negotiation(
                vacancy_id=vacancy_id,
                resume_id=resume_id,
            )
            return None if negotiation_id is None else str(negotiation_id)
        except (hh_errors.BadResponse, requests.RequestException):
            return None
        finally:
            self._persist(tool)

    def _tool(self, account_key: str, *, deadline: float) -> Any:
        profile = (self._config_dir / account_key).resolve()
        root = self._config_dir.resolve()
        if profile.parent != root:
            raise ValueError(f"unsafe HH account key: {account_key!r}")
        if not (profile / "config.json").is_file():
            raise RuntimeError(f"HH applicant profile is not configured: {account_key!r}")

        tool = HHApplicantTool.__new__(HHApplicantTool)
        tool.config_dir = root
        tool.profile_id = account_key
        tool.api_delay = None
        tool.user_agent = None
        tool.proxy_url = None
        tool.openai_proxy_url = None
        tool.openai_timeout = None
        tool.openai_connect_timeout = None
        self._install_request_timeout(tool, deadline=deadline)
        return tool

    def _install_request_timeout(self, tool: Any, *, deadline: float) -> None:
        session = tool.session
        original_request = session.request
        timeout = self._request_timeout_seconds

        def request_with_timeout(method: str, url: str, **kwargs: Any) -> Any:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise requests.Timeout("HH applicant operation deadline exceeded")
            kwargs["timeout"] = min(timeout, remaining)
            return original_request(method, url, **kwargs)

        session.request = request_with_timeout

    @staticmethod
    def _persist(tool: Any) -> None:
        try:
            tool.save_token()
        finally:
            tool.save_cookies()
