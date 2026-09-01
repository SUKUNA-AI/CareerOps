"""Guard HH submissions and persist their complete asynchronous S3 audit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4


class HHApplicationBlocked(RuntimeError):
    """Signal that current HH vacancy state makes submission unsafe."""


class AuditObjectRef(Protocol):
    """Minimal persisted-object reference required by the audit service."""

    @property
    def uri(self) -> str:
        """Return the immutable audit URI."""

        ...


class JsonAuditStore(Protocol):
    """Asynchronous JSON writer used by the application audit boundary."""

    async def put_json(
        self,
        key: str,
        payload: Any,
        *,
        collected_at: datetime | None = None,
    ) -> AuditObjectRef:
        """Persist one JSON audit object."""

        ...


class HHApplicationDriver(Protocol):
    """Existing synchronous HH adapter used for external API operations."""

    def fetch_vacancy(self, vacancy_id: str | int) -> dict[str, Any]:
        """Fetch current vacancy state from HH."""

        ...

    def submit_application(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> dict[str, Any]:
        """Submit a standard HH negotiation application."""

        ...

    def submit_application_with_test(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> dict[str, Any]:
        """Submit through the existing HH test-aware route."""

        ...


@dataclass(frozen=True, slots=True)
class AuditedApplicationResult:
    """References and outcome returned after a fully audited application."""

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
    """Return the current UTC audit timestamp."""

    return datetime.now(UTC)


def _company_name(vacancy: dict[str, Any]) -> str | None:
    """Extract the optional employer name from an HH vacancy payload."""

    employer = vacancy.get("employer") or {}
    return employer.get("name")


def _validate_before_submit(vacancy: dict[str, Any]) -> None:
    """Reject already handled, closed, archived, or externally-routed vacancies."""

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
    """Select the existing HH submission route from the vacancy contract."""

    return "upstream_hh_test" if vacancy.get("has_test") else "negotiations_api"


class HHApplicationAuditService:
    """Execute the unchanged HH submission flow around async immutable audits."""

    def __init__(
        self,
        *,
        driver: HHApplicationDriver,
        store: JsonAuditStore,
        profile_id: str,
    ) -> None:
        """Bind the HH driver, async audit store, and producer profile id."""

        self.driver = driver
        self.store = store
        self.profile_id = profile_id

    async def apply(
        self,
        *,
        vacancy_id: str,
        resume_id: str,
        message: str,
        run_id: UUID | None = None,
        before: dict[str, Any] | None = None,
    ) -> AuditedApplicationResult:
        """Validate, submit, confirm, and persist all four application objects."""

        run_id = run_id or uuid4()
        started_at = _now()

        before = before or self.driver.fetch_vacancy(vacancy_id)
        before_collected_at = _now()
        _validate_before_submit(before)
        submission_mode = _submission_mode(before)

        prefix = (
            "applications/"
            f"date={started_at.date().isoformat()}/"
            f"run_id={run_id}/"
            f"vacancy_id={vacancy_id}"
        )

        before_ref = await self.store.put_json(
            f"{prefix}/vacancy_before.json",
            before,
            collected_at=before_collected_at,
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
        request_ref = await self.store.put_json(
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
            result_ref = await self.store.put_json(
                f"{prefix}/application_result.json",
                failed_payload,
            )
            raise RuntimeError(
                "HH application failed after request audit was persisted. "
                f"Result: {result_ref.uri}"
            ) from exc

        after = self.driver.fetch_vacancy(vacancy_id)
        after_collected_at = _now()
        after_ref = await self.store.put_json(
            f"{prefix}/vacancy_after.json",
            after,
            collected_at=after_collected_at,
        )

        relations = tuple(str(x) for x in (after.get("relations") or []))
        confirmed = "got_response" in relations

        status = "submitted" if confirmed else "unconfirmed"
        result_payload = {
            "event_type": "hh.application.submitted",
            "schema_version": 2,
            "run_id": str(run_id),
            "profile_id": self.profile_id,
            "resume_id": resume_id,
            "vacancy_id": vacancy_id,
            "submission_mode": submission_mode,
            "status": status,
            "confirmed": confirmed,
            "relations": list(relations),
            "upstream_response": upstream_result,
            "finished_at": _now().isoformat(),
        }
        result_ref = await self.store.put_json(
            f"{prefix}/application_result.json",
            result_payload,
        )

        return AuditedApplicationResult(
            run_id=run_id,
            vacancy_id=vacancy_id,
            status=status,
            confirmed=confirmed,
            submission_mode=submission_mode,
            prefix=prefix,
            before_uri=before_ref.uri,
            request_uri=request_ref.uri,
            result_uri=result_ref.uri,
            after_uri=after_ref.uri,
        )
