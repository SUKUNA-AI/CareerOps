"""Guard HH submissions with persistent claims and immutable S3 audit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from .application_claims import (
    ApplicationClaimStatus,
    ApplicationClaimStore,
    ApplicationIdentity,
)
from .runtime import HHExternalWriteGuard


class HHApplicationBlocked(RuntimeError):
    """Signal that current durable or HH state makes submission unsafe."""


class HHApplicationUncertain(RuntimeError):
    """Signal that the external result is ambiguous and must not be retried blindly."""

    def __init__(self, message: str, *, external_write_attempted: bool) -> None:
        super().__init__(message)
        self.external_write_attempted = external_write_attempted


class AuditObjectRef(Protocol):
    @property
    def uri(self) -> str:
        """Return the immutable audit URI."""

        ...

    @property
    def sha256(self) -> str:
        """Return the immutable audit payload digest."""

        ...


class JsonAuditStore(Protocol):
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
    def fetch_vacancy(self, vacancy_id: str | int) -> dict[str, Any]:
        """Fetch current vacancy state from HH."""

        ...

    def find_application_evidence(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
    ) -> dict[str, Any]:
        """Read resume-specific negotiation evidence through existing transport."""

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
    run_id: UUID
    vacancy_id: str
    status: str
    confirmed: bool
    submission_mode: str
    claim_status: ApplicationClaimStatus
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


def _validate_before_submit(
    vacancy: dict[str, Any],
    *,
    expected_vacancy_id: str,
) -> None:
    """Reject structural blockers; global relations are not resume-specific."""

    vacancy_id = str(vacancy.get("id", "?"))
    if vacancy_id != expected_vacancy_id:
        raise ValueError(
            "authoritative HH vacancy identity mismatch: "
            f"expected={expected_vacancy_id!r}, actual={vacancy_id!r}"
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


def _validate_evidence(
    evidence: dict[str, Any],
    identity: ApplicationIdentity,
) -> bool:
    """Require evidence to prove the exact resume-vacancy pair or fail closed."""

    if evidence.get("source_profile") != identity.source_profile:
        raise ValueError("HH application evidence source profile mismatch")
    if evidence.get("source_resume_id") != identity.source_resume_id:
        raise ValueError("HH application evidence resume identity mismatch")
    if evidence.get("vacancy_id") != identity.vacancy_id:
        raise ValueError("HH application evidence vacancy identity mismatch")
    found = evidence.get("found")
    if not isinstance(found, bool):
        raise ValueError("HH application evidence has no boolean found field")
    return found


class HHApplicationAuditService:
    """Submit one identity only after a committed, atomically unique claim."""

    def __init__(
        self,
        *,
        driver: HHApplicationDriver,
        store: JsonAuditStore,
        claim_store: ApplicationClaimStore,
        account_key: str,
        profile_id: str,
        external_write_guard: HHExternalWriteGuard | None = None,
        application_context: dict[str, Any] | None = None,
    ) -> None:
        self.driver = driver
        self.store = store
        self.claim_store = claim_store
        self.account_key = account_key.strip()
        self.profile_id = profile_id.strip()
        if not self.account_key or not self.profile_id:
            raise ValueError("account_key and profile_id must not be empty")
        self.external_write_guard = external_write_guard or HHExternalWriteGuard()
        self.application_context = {
            key: value
            for key, value in (application_context or {}).items()
            if value is not None
        }

    async def _mark_uncertain(
        self,
        *,
        identity: ApplicationIdentity,
        run_id: UUID,
        expected: tuple[ApplicationClaimStatus, ...],
        exc: BaseException,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        await self.claim_store.transition(
            identity=identity,
            application_run_id=run_id,
            expected=expected,
            status=ApplicationClaimStatus.UNCERTAIN,
            changed_at=_now(),
            error_type=type(exc).__name__,
            error_message=str(exc),
            upstream_evidence=evidence,
        )

    async def apply(
        self,
        *,
        vacancy_id: str,
        resume_id: str,
        message: str,
        run_id: UUID | None = None,
        before: dict[str, Any] | None = None,
    ) -> AuditedApplicationResult:
        """Claim, precheck exact identity, submit once, and confirm resume-specifically."""

        self.external_write_guard.require("HH application submission")
        run_id = run_id or uuid4()
        started_at = _now()
        identity = ApplicationIdentity(
            source_profile=self.profile_id,
            source_resume_id=resume_id,
            vacancy_id=vacancy_id,
        )

        before = before or self.driver.fetch_vacancy(vacancy_id)
        before_collected_at = _now()
        _validate_before_submit(before, expected_vacancy_id=identity.vacancy_id)
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
        await self.claim_store.prepare_identity(
            identity=identity,
            account_key=self.account_key,
            vacancy=before,
            observed_at=before_collected_at,
            raw_uri=before_ref.uri,
            content_hash=before_ref.sha256,
        )

        acquisition = await self.claim_store.acquire(
            identity=identity,
            account_key=self.account_key,
            application_run_id=run_id,
            claimed_at=started_at,
        )
        if not acquisition.acquired:
            current = acquisition.record
            raise HHApplicationBlocked(
                "application identity already has persistent claim "
                f"status={current.status.value}, run_id={current.application_run_id}"
            )

        try:
            precheck_evidence = self.driver.find_application_evidence(
                resume_id=resume_id,
                vacancy_id=vacancy_id,
            )
            already_exists = _validate_evidence(precheck_evidence, identity)
        except Exception as exc:
            await self._mark_uncertain(
                identity=identity,
                run_id=run_id,
                expected=(ApplicationClaimStatus.CLAIMED,),
                exc=exc,
            )
            raise HHApplicationUncertain(
                "resume-specific HH precheck is uncertain; automatic POST is forbidden",
                external_write_attempted=False,
            ) from exc

        if already_exists:
            await self.claim_store.transition(
                identity=identity,
                application_run_id=run_id,
                expected=(ApplicationClaimStatus.CLAIMED,),
                status=ApplicationClaimStatus.SUBMITTED,
                changed_at=_now(),
                upstream_evidence=precheck_evidence,
            )
            raise HHApplicationBlocked(
                "resume-specific HH negotiation already exists for this vacancy"
            )

        try:
            request_payload = {
                "event_type": "hh.application.requested",
                "schema_version": 2,
                "run_id": str(run_id),
                "account_key": self.account_key,
                "profile_id": self.profile_id,
                "resume_id": resume_id,
                "vacancy_id": vacancy_id,
                "vacancy_title": before.get("name"),
                "company_name": _company_name(before),
                "message": message,
                "submission_mode": submission_mode,
                "has_test": bool(before.get("has_test")),
                "careerops_binding": self.application_context,
                "claim_status": ApplicationClaimStatus.CLAIMED.value,
                "requested_at": started_at.isoformat(),
            }
            request_ref = await self.store.put_json(
                f"{prefix}/application_request.json",
                request_payload,
            )
        except Exception as exc:
            await self.claim_store.transition(
                identity=identity,
                application_run_id=run_id,
                expected=(ApplicationClaimStatus.CLAIMED,),
                status=ApplicationClaimStatus.FAILED_SAFE_TO_RETRY,
                changed_at=_now(),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

        await self.claim_store.transition(
            identity=identity,
            application_run_id=run_id,
            expected=(ApplicationClaimStatus.CLAIMED,),
            status=ApplicationClaimStatus.SUBMITTING,
            changed_at=_now(),
            upstream_evidence=precheck_evidence,
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
            await self._mark_uncertain(
                identity=identity,
                run_id=run_id,
                expected=(ApplicationClaimStatus.SUBMITTING,),
                exc=exc,
                evidence=precheck_evidence,
            )
            failed_payload = {
                "event_type": "hh.application.uncertain",
                "schema_version": 2,
                "run_id": str(run_id),
                "account_key": self.account_key,
                "profile_id": self.profile_id,
                "resume_id": resume_id,
                "vacancy_id": vacancy_id,
                "submission_mode": submission_mode,
                "status": "uncertain",
                "claim_status": ApplicationClaimStatus.UNCERTAIN.value,
                "careerops_binding": self.application_context,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "finished_at": _now().isoformat(),
            }
            try:
                await self.store.put_json(
                    f"{prefix}/application_result.json",
                    failed_payload,
                )
            except Exception:
                pass
            raise HHApplicationUncertain(
                "HH write result is ambiguous; claim is UNCERTAIN and retry is blocked",
                external_write_attempted=True,
            ) from exc

        after_uri: str | None = None
        confirmation_error: dict[str, str] | None = None
        try:
            after = self.driver.fetch_vacancy(vacancy_id)
            after_ref = await self.store.put_json(
                f"{prefix}/vacancy_after.json",
                after,
                collected_at=_now(),
            )
            after_uri = after_ref.uri
        except Exception as exc:
            confirmation_error = {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "stage": "vacancy_after",
            }

        confirmation_evidence: dict[str, Any] | None = None
        try:
            confirmation_evidence = self.driver.find_application_evidence(
                resume_id=resume_id,
                vacancy_id=vacancy_id,
            )
            confirmed = _validate_evidence(confirmation_evidence, identity)
        except Exception as exc:
            confirmed = False
            confirmation_error = {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "stage": "resume_specific_confirmation",
            }

        try:
            await self.claim_store.transition(
                identity=identity,
                application_run_id=run_id,
                expected=(ApplicationClaimStatus.SUBMITTING,),
                status=ApplicationClaimStatus.SUBMITTED,
                changed_at=_now(),
                upstream_evidence={
                    "precheck": precheck_evidence,
                    "confirmation": confirmation_evidence,
                    "confirmation_error": confirmation_error,
                },
            )
        except Exception as exc:
            raise HHApplicationUncertain(
                "HH POST returned, but its durable claim could not be finalized; "
                "the SUBMITTING identity must not be retried automatically",
                external_write_attempted=True,
            ) from exc

        status = "submitted" if confirmed else "submitted_unconfirmed"
        result_payload: dict[str, Any] = {
            "event_type": "hh.application.submitted",
            "schema_version": 2,
            "run_id": str(run_id),
            "account_key": self.account_key,
            "profile_id": self.profile_id,
            "resume_id": resume_id,
            "vacancy_id": vacancy_id,
            "submission_mode": submission_mode,
            "status": status,
            "confirmed": confirmed,
            "claim_status": ApplicationClaimStatus.SUBMITTED.value,
            "confirmation_evidence": confirmation_evidence,
            "careerops_binding": self.application_context,
            "vacancy_before_uri": before_ref.uri,
            "application_request_uri": request_ref.uri,
            "vacancy_after_uri": after_uri,
            "upstream_response": upstream_result,
            "finished_at": _now().isoformat(),
        }
        if confirmation_error is not None:
            result_payload["confirmation_error"] = confirmation_error
        try:
            result_ref = await self.store.put_json(
                f"{prefix}/application_result.json",
                result_payload,
            )
        except Exception as exc:
            raise HHApplicationUncertain(
                "HH POST and durable SUBMITTED claim succeeded, but the final "
                "S3 audit write failed; automatic retry is forbidden",
                external_write_attempted=True,
            ) from exc

        return AuditedApplicationResult(
            run_id=run_id,
            vacancy_id=vacancy_id,
            status=status,
            confirmed=confirmed,
            submission_mode=submission_mode,
            claim_status=ApplicationClaimStatus.SUBMITTED,
            prefix=prefix,
            before_uri=before_ref.uri,
            request_uri=request_ref.uri,
            result_uri=result_ref.uri,
            after_uri=after_uri,
        )
