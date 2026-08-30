from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4


class HHApplicationBlocked(RuntimeError):
    pass


class JsonAuditStore(Protocol):
    def put_json(self, key: str, payload: Any): ...


class HHApplicationDriver(Protocol):
    def fetch_vacancy(self, vacancy_id: str | int) -> dict[str, Any]: ...

    def submit_application(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> dict[str, Any]: ...

    def submit_application_with_test(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class AuditedApplicationResult:
    run_id: UUID
    vacancy_id: str
    status: str
    confirmed: bool
    submission_mode: str
    prefix: str
    before_uri: str
    request_uri: str
    result_uri: str
    after_uri: str | None


def _now() -> datetime:
    return datetime.now(UTC)


def _company_name(vacancy: dict[str, Any]) -> str | None:
    employer = vacancy.get("employer") or {}
    return employer.get("name")


def _validate_before_submit(vacancy: dict[str, Any]) -> None:
    vacancy_id = str(vacancy.get("id", "?"))

    relations = vacancy.get("relations") or []
    if relations:
        raise HHApplicationBlocked(
            f"vacancy {vacancy_id} already has HH relations={relations!r}"
        )

    if vacancy.get("archived"):
        raise HHApplicationBlocked(f"vacancy {vacancy_id} is archived")

    if vacancy.get("closed_for_applicants"):
        raise HHApplicationBlocked(
            f"vacancy {vacancy_id} is closed_for_applicants"
        )

    if vacancy.get("response_url"):
        raise HHApplicationBlocked(
            f"vacancy {vacancy_id} uses external response_url"
        )


def _submission_mode(vacancy: dict[str, Any]) -> str:
    return "upstream_hh_test" if vacancy.get("has_test") else "negotiations_api"


class HHApplicationAuditService:
    def __init__(
        self,
        *,
        driver: HHApplicationDriver,
        store: JsonAuditStore,
        profile_id: str,
    ) -> None:
        self.driver = driver
        self.store = store
        self.profile_id = profile_id

    def apply(
        self,
        *,
        vacancy_id: str,
        resume_id: str,
        message: str,
        run_id: UUID | None = None,
    ) -> AuditedApplicationResult:
        run_id = run_id or uuid4()
        started_at = _now()

        before = self.driver.fetch_vacancy(vacancy_id)
        _validate_before_submit(before)
        submission_mode = _submission_mode(before)

        prefix = (
            "applications/"
            f"date={started_at.date().isoformat()}/"
            f"run_id={run_id}/"
            f"vacancy_id={vacancy_id}"
        )

        before_ref = self.store.put_json(
            f"{prefix}/vacancy_before.json",
            before,
        )

        request_payload = {
            "event_type": "hh.application.requested",
            "schema_version": 2,
            "run_id": str(run_id),
            "profile_id": self.profile_id,
            "resume_id": resume_id,
            "vacancy_id": vacancy_id,
            "vacancy_title": before.get("name"),
            "company_name": _company_name(before),
            "message": message,
            "submission_mode": submission_mode,
            "has_test": bool(before.get("has_test")),
            "requested_at": started_at.isoformat(),
        }
        request_ref = self.store.put_json(
            f"{prefix}/application_request.json",
            request_payload,
        )

        try:
            if submission_mode == "upstream_hh_test":
                upstream_result = self.driver.submit_application_with_test(
                    resume_id=resume_id,
                    vacancy_id=vacancy_id,
                    message=message,
                )
            else:
                upstream_result = self.driver.submit_application(
                    resume_id=resume_id,
                    vacancy_id=vacancy_id,
                    message=message,
                )
        except Exception as exc:
            failed_payload = {
                "event_type": "hh.application.failed",
                "schema_version": 2,
                "run_id": str(run_id),
                "profile_id": self.profile_id,
                "resume_id": resume_id,
                "vacancy_id": vacancy_id,
                "submission_mode": submission_mode,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "finished_at": _now().isoformat(),
            }
            result_ref = self.store.put_json(
                f"{prefix}/application_result.json",
                failed_payload,
            )
            raise RuntimeError(
                "HH application failed after request audit was persisted. "
                f"Result: {result_ref.uri}"
            ) from exc

        after = self.driver.fetch_vacancy(vacancy_id)
        after_ref = self.store.put_json(
            f"{prefix}/vacancy_after.json",
            after,
        )

        relations = tuple(str(x) for x in (after.get("relations") or []))
        confirmed = "got_response" in relations

        result_payload = {
            "event_type": "hh.application.submitted",
            "schema_version": 2,
            "run_id": str(run_id),
            "profile_id": self.profile_id,
            "resume_id": resume_id,
            "vacancy_id": vacancy_id,
            "submission_mode": submission_mode,
            "status": "submitted" if confirmed else "unconfirmed",
            "confirmed": confirmed,
            "relations": list(relations),
            "upstream_response": upstream_result,
            "finished_at": _now().isoformat(),
        }
        result_ref = self.store.put_json(
            f"{prefix}/application_result.json",
            result_payload,
        )

        return AuditedApplicationResult(
            run_id=run_id,
            vacancy_id=vacancy_id,
            status=result_payload["status"],
            confirmed=confirmed,
            submission_mode=submission_mode,
            prefix=prefix,
            before_uri=before_ref.uri,
            request_uri=request_ref.uri,
            result_uri=result_ref.uri,
            after_uri=after_ref.uri,
        )
