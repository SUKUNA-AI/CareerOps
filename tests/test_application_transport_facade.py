from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import careerops_application.infrastructure.hh_applicant_transport as transport_module
from careerops_application.domain import TransportPrecheckState, TransportSubmitState
from careerops_application.infrastructure.hh_applicant_transport import HHApplicantTransport


class _Vendor:
    def __init__(self, *, precheck_error: BaseException | None = None, submit_error: BaseException | None = None) -> None:
        self.precheck_error = precheck_error
        self.submit_error = submit_error

    def precheck(self, **kwargs: object) -> object:
        del kwargs
        if self.precheck_error is not None:
            raise self.precheck_error
        return SimpleNamespace(already_submitted=False, ready=True, questions=(), reason=None)

    def submit(self, **kwargs: object) -> object:
        del kwargs
        if self.submit_error is not None:
            raise self.submit_error
        return SimpleNamespace(negotiation_id="neg-1")

    def find_negotiation(self, **kwargs: object) -> str | None:
        del kwargs
        return None


def _transport(monkeypatch: pytest.MonkeyPatch, vendor: _Vendor) -> HHApplicantTransport:
    transport = HHApplicantTransport(config_dir=Path("."))
    monkeypatch.setattr(transport, "_tool", lambda _account_key: object())
    monkeypatch.setattr(transport, "_persist", lambda _tool: None)
    monkeypatch.setattr(transport_module, "VendorApplicantTransport", lambda _tool: vendor)
    return transport


@pytest.mark.parametrize(
    ("error", "state", "reason"),
    [
        (
            requests.RequestException("network"),
            TransportPrecheckState.SAFE_FAILURE,
            "application.hh_transport_unavailable",
        ),
    ],
)
def test_precheck_transport_exception_is_safe_before_submit(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    state: TransportPrecheckState,
    reason: str,
) -> None:
    transport = _transport(monkeypatch, _Vendor(precheck_error=error))
    result = transport._precheck_sync("primary", "123", "resume", "")
    assert result.state is state
    assert result.reason_code == reason


def test_precheck_auth_exception_blocks_without_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Forbidden(Exception):
        pass

    monkeypatch.setattr(transport_module.hh_errors, "Forbidden", _Forbidden)
    transport = _transport(monkeypatch, _Vendor(precheck_error=_Forbidden()))
    result = transport._precheck_sync("primary", "123", "resume", "")
    assert result.state is TransportPrecheckState.BLOCKED
    assert result.reason_code == "application.hh_auth_required"


def test_submit_network_exception_is_uncertain_never_safe_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _transport(
        monkeypatch,
        _Vendor(submit_error=requests.RequestException("connection reset after POST")),
    )
    result = transport._submit_sync("primary", "123", "resume", "", {})
    assert result.state is TransportSubmitState.UNCERTAIN
    assert result.reason_code == "application.submit_outcome_unknown"


def test_submit_questionnaire_exception_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    class _QuestionnaireRequired(Exception):
        pass

    monkeypatch.setattr(transport_module, "QuestionnaireRequired", _QuestionnaireRequired)
    transport = _transport(monkeypatch, _Vendor(submit_error=_QuestionnaireRequired()))
    result = transport._submit_sync("primary", "123", "resume", "", {})
    assert result.state is TransportSubmitState.BLOCKED
    assert result.reason_code == "application.questionnaire_required"


def test_find_submission_transport_failure_is_unknown_not_absence_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FindVendor(_Vendor):
        def find_negotiation(self, **kwargs: object) -> str | None:
            del kwargs
            raise requests.RequestException("network")

    transport = _transport(monkeypatch, _FindVendor())
    assert transport._find_submission_sync("primary", "123", "resume") is None
