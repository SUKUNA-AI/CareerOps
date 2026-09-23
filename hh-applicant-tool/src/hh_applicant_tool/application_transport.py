from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

import requests

from .api.errors import Redirect

if TYPE_CHECKING:
    from .main import HHApplicantTool


@dataclass(frozen=True, slots=True)
class QuestionnaireQuestion:
    question_id: str
    text: str
    options: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class PrecheckResult:
    ready: bool
    already_submitted: bool = False
    reason: str | None = None
    negotiation_id: str | None = None
    questions: tuple[QuestionnaireQuestion, ...] = ()


@dataclass(frozen=True, slots=True)
class SubmitResult:
    submitted: bool
    negotiation_id: str | None = None


class ApplicantTransport:
    """Narrow applicant primitive. It never searches vacancies or decides relevance."""

    def __init__(self, tool: HHApplicantTool) -> None:
        self._tool = tool

    def precheck(
        self,
        *,
        vacancy_id: str,
        resume_id: str,
        cover_letter: str,
    ) -> PrecheckResult:
        resume = self._find_resume(resume_id)
        if resume is None:
            return PrecheckResult(ready=False, reason="resume_not_found")
        if (resume.get("status") or {}).get("id") != "published":
            return PrecheckResult(ready=False, reason="resume_not_published")

        negotiation_id = self.find_negotiation(vacancy_id=vacancy_id, resume_id=resume_id)
        if negotiation_id is not None:
            return PrecheckResult(
                ready=False,
                already_submitted=True,
                reason="already_submitted",
                negotiation_id=negotiation_id,
            )

        vacancy: dict[str, Any] = self._tool.api_client.get(f"/vacancies/{vacancy_id}")
        if vacancy.get("archived"):
            return PrecheckResult(ready=False, reason="vacancy_archived")
        if vacancy.get("response_letter_required") and not cover_letter.strip():
            return PrecheckResult(ready=False, reason="cover_letter_required")

        if vacancy.get("has_test"):
            questions = self._load_questions(vacancy_id)
            if questions:
                return PrecheckResult(
                    ready=False,
                    reason="questionnaire_required",
                    questions=questions,
                )

        return PrecheckResult(ready=True)

    def submit(
        self,
        *,
        vacancy_id: str,
        resume_id: str,
        cover_letter: str,
        questionnaire_answers: Mapping[str, str],
    ) -> SubmitResult:
        try:
            if questionnaire_answers:
                self._submit_questionnaire(
                    vacancy_id=vacancy_id,
                    resume_id=resume_id,
                    cover_letter=cover_letter,
                    answers=questionnaire_answers,
                )
            else:
                try:
                    response: dict[str, Any] = self._tool.api_client.post(
                        "/negotiations",
                        {
                            "resume_id": resume_id,
                            "vacancy_id": vacancy_id,
                            "message": cover_letter,
                        },
                    )
                except Redirect:
                    questions = self._load_questions(vacancy_id)
                    if questions:
                        raise QuestionnaireRequired(questions) from None
                    raise
                if response not in ({}, None):
                    raise UnexpectedSubmissionResponse(response)

            negotiation_id = self.find_negotiation(
                vacancy_id=vacancy_id,
                resume_id=resume_id,
            )
            return SubmitResult(submitted=True, negotiation_id=negotiation_id)
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            raise SubmissionOutcomeUnknown(str(exc)) from exc

    def find_negotiation(self, *, vacancy_id: str, resume_id: str) -> str | None:
        for negotiation in self._tool.get_negotiations():
            vacancy = negotiation.get("vacancy") or {}
            resume = negotiation.get("resume") or {}
            if str(vacancy.get("id")) == str(vacancy_id) and str(resume.get("id")) == str(
                resume_id
            ):
                value = negotiation.get("id")
                return None if value is None else str(value)
        return None

    def _find_resume(self, resume_id: str) -> dict[str, Any] | None:
        for resume in self._tool.get_resumes():
            if str(resume.get("id")) == str(resume_id):
                return dict(resume)
        return None

    def _load_questions(self, vacancy_id: str) -> tuple[QuestionnaireQuestion, ...]:
        response_url = (
            "https://hh.ru/applicant/vacancy_response"
            f"?vacancyId={vacancy_id}&startedWithQuestion=false&hhtmFrom=vacancy"
        )
        config = self._tool.get_redirect_config(response_url)
        tests = _find_key(config, "vacancyTests")
        if not isinstance(tests, dict):
            return ()
        test = tests.get(str(vacancy_id))
        if not isinstance(test, dict):
            return ()

        questions: list[QuestionnaireQuestion] = []
        for raw in test.get("tasks") or []:
            if not isinstance(raw, dict):
                continue
            question_id = str(raw.get("id") or "").strip()
            if not question_id:
                continue
            options = tuple(
                (str(item.get("id")), str(item.get("text") or ""))
                for item in raw.get("candidateSolutions") or []
                if isinstance(item, dict) and item.get("id") is not None
            )
            questions.append(
                QuestionnaireQuestion(
                    question_id=question_id,
                    text=str(raw.get("description") or "").strip(),
                    options=options,
                )
            )
        return tuple(questions)

    def _submit_questionnaire(
        self,
        *,
        vacancy_id: str,
        resume_id: str,
        cover_letter: str,
        answers: Mapping[str, str],
    ) -> None:
        response_url = (
            "https://hh.ru/applicant/vacancy_response"
            f"?vacancyId={vacancy_id}&startedWithQuestion=false&hhtmFrom=vacancy"
        )
        config = self._tool.get_redirect_config(response_url)
        tests = _find_key(config, "vacancyTests")
        if not isinstance(tests, dict) or not isinstance(tests.get(str(vacancy_id)), dict):
            raise ValueError(f"questionnaire metadata not found for vacancy {vacancy_id}")
        test: dict[str, Any] = tests[str(vacancy_id)]
        payload: dict[str, Any] = {
            "_xsrf": self._tool.xsrf_token,
            "uidPk": test["uidPk"],
            "guid": test["guid"],
            "startTime": test["startTime"],
            "testRequired": test["required"],
            "vacancy_id": vacancy_id,
            "resume_hash": resume_id,
            "ignore_postponed": "true",
            "incomplete": "false",
            "mark_applicant_visible_in_vacancy_country": "false",
            "country_ids": "[]",
            "lux": "true",
            "withoutTest": "no",
            "letter": cover_letter,
        }
        missing: list[str] = []
        for raw in test.get("tasks") or []:
            if not isinstance(raw, dict) or raw.get("id") is None:
                continue
            question_id = str(raw["id"])
            answer = answers.get(question_id)
            if answer is None:
                missing.append(question_id)
                continue
            if raw.get("candidateSolutions"):
                payload[f"task_{question_id}"] = answer
            else:
                payload[f"task_{question_id}_text"] = answer
        if missing:
            raise ValueError(f"missing questionnaire answers: {', '.join(missing)}")

        response = self._tool.session.post(
            "https://hh.ru/applicant/vacancy_response/popup",
            data=payload,
            headers={
                "Referer": response_url,
                "X-Hhtmfrom": "vacancy",
                "X-Hhtmsource": "vacancy_response",
                "X-Requested-With": "XMLHttpRequest",
                "X-Xsrftoken": self._tool.xsrf_token,
            },
            timeout=30,
        )
        if response.status_code >= 500:
            raise requests.HTTPError(
                f"questionnaire submission returned {response.status_code}",
                response=response,
            )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise UnexpectedSubmissionResponse(response.text[:300]) from exc
        if not isinstance(data, dict) or str(data.get("success")).lower() != "true":
            raise UnexpectedSubmissionResponse(data)


class QuestionnaireRequired(RuntimeError):
    def __init__(self, questions: tuple[QuestionnaireQuestion, ...]) -> None:
        super().__init__("questionnaire answers are required")
        self.questions = questions


class SubmissionOutcomeUnknown(RuntimeError):
    pass


class UnexpectedSubmissionResponse(SubmissionOutcomeUnknown):
    def __init__(self, payload: object) -> None:
        super().__init__(f"unexpected applicant submission response: {payload!r}")


def _find_key(value: object, key: str) -> object | None:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for item in value.values():
            found = _find_key(item, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_key(item, key)
            if found is not None:
                return found
    return None
