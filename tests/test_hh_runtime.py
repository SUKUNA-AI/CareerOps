from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from careerops_integrations.hh.application_audit import HHApplicationAuditService
from careerops_integrations.hh.application_cli import _parser as application_parser
from careerops_integrations.hh.application_cli import _run as run_application_cli
from careerops_integrations.hh.batch_cli import _parser, _resolve_mode
from careerops_integrations.hh.driver import HHApplicantToolCLI
from careerops_integrations.hh.runtime import (
    HHExternalWriteForbidden,
    HHExternalWriteGuard,
    RuntimeMode,
)
from careerops_integrations.hh.test_bridge import submit_vacancy_test_via_upstream


def test_exactly_two_modes_and_default_observe() -> None:
    assert list(RuntimeMode) == [RuntimeMode.OBSERVE, RuntimeMode.APPLY]
    assert RuntimeMode.parse(None) is RuntimeMode.OBSERVE


def test_batch_cli_defaults_to_postgres_resume_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CAREEROPS_HH_RESUME_REGISTRY", raising=False)
    args = _parser().parse_args([])
    assert args.resume_registry == "postgres"


def test_invalid_mode_fails_closed() -> None:
    with pytest.raises(ValueError, match="invalid HH runtime mode"):
        RuntimeMode.parse("shadow")


@pytest.mark.parametrize(
    "guard",
    [
        HHExternalWriteGuard(RuntimeMode.OBSERVE, False),
        HHExternalWriteGuard(RuntimeMode.OBSERVE, True),
        HHExternalWriteGuard(RuntimeMode.APPLY, False),
    ],
)
def test_external_write_requires_both_independent_conditions(
    guard: HHExternalWriteGuard,
) -> None:
    with pytest.raises(HHExternalWriteForbidden):
        guard.require("test")


def test_apply_with_explicit_external_opt_in_is_write_capable() -> None:
    guard = HHExternalWriteGuard(RuntimeMode.APPLY, True)
    guard.require("test")
    assert guard.external_writes_allowed is True


def test_driver_blocks_normal_and_test_submission_in_observe() -> None:
    driver = HHApplicantToolCLI(config_dir=Path("config"), profile="profile")
    with pytest.raises(HHExternalWriteForbidden):
        driver.submit_application(resume_id="r", vacancy_id="v", message="m")
    with pytest.raises(HHExternalWriteForbidden):
        driver.submit_application_with_test(
            resume_id="r",
            vacancy_id="v",
            message="m",
        )


def test_direct_test_bridge_also_requires_central_write_capability() -> None:
    with pytest.raises(HHExternalWriteForbidden):
        submit_vacancy_test_via_upstream(
            config_dir=Path("config"),
            profile="profile",
            vacancy_id="v",
            resume_id="r",
            message="m",
            external_write_guard=HHExternalWriteGuard(),
        )


def test_driver_delegates_profile_auth_and_session_to_hh_applicant_tool() -> None:
    driver = HHApplicantToolCLI(
        config_dir=Path("hh-applicant-tool/config"),
        profile="careerops-junior",
        python_executable=Path("python"),
    )
    assert driver._base_command() == [
        "python",
        "-m",
        "hh_applicant_tool",
        "--config-dir",
        str(Path("hh-applicant-tool/config").resolve()),
        "--profile",
        "careerops-junior",
    ]


def test_driver_exposes_exact_ordered_search_pages_before_dedup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = HHApplicantToolCLI(config_dir=Path("config"), profile="profile")
    payloads = [
        {"items": [{"id": "1"}, {"id": "1"}], "page": 0, "pages": 2},
        {"items": [{"id": "2"}], "page": 1, "pages": 2},
    ]

    def fake_call_api(endpoint: str, *, params: dict[str, Any]):
        assert endpoint == "vacancies"
        return payloads[int(params["page"])]

    monkeypatch.setattr(driver, "call_api", fake_call_api)
    pages = list(driver.search_vacancy_pages(text="ML", pages=3))
    assert [page.page for page in pages] == [0, 1]
    assert [page.payload for page in pages] == payloads
    assert driver.search_vacancies(text="ML", pages=3) == [
        {"id": "1"},
        {"id": "2"},
    ]


def test_driver_requires_authoritative_resume_items_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = HHApplicantToolCLI(config_dir=Path("config"), profile="profile")
    monkeypatch.setattr(
        driver,
        "call_api",
        lambda endpoint, **kwargs: {"unexpected": [], "pages": 1},
    )
    with pytest.raises(RuntimeError, match="items is not a list"):
        driver.list_resumes()


def test_driver_reads_every_resume_inventory_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = HHApplicantToolCLI(config_dir=Path("config"), profile="profile")
    calls: list[dict[str, Any]] = []

    def fake_call_api(endpoint: str, *, params: dict[str, Any]) -> dict[str, Any]:
        assert endpoint == "resumes/mine"
        calls.append(params)
        page = int(params["page"])
        return {
            "items": [{"id": f"resume-{page}"}],
            "page": page,
            "pages": 2,
        }

    monkeypatch.setattr(driver, "call_api", fake_call_api)

    assert driver.list_resumes() == [{"id": "resume-0"}, {"id": "resume-1"}]
    assert calls == [
        {"page": 0, "per_page": 100},
        {"page": 1, "per_page": 100},
    ]


def test_driver_refuses_incomplete_negotiation_pagination_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = HHApplicantToolCLI(config_dir=Path("config"), profile="profile")
    monkeypatch.setattr(
        driver,
        "call_api",
        lambda endpoint, **kwargs: {"items": []},
    )

    with pytest.raises(RuntimeError, match="pages is not"):
        driver.find_application_evidence(resume_id="resume", vacancy_id="vacancy")


def test_driver_negotiation_evidence_is_exactly_resume_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = HHApplicantToolCLI(config_dir=Path("config"), profile="profile")

    def fake_call_api(endpoint: str, **kwargs: Any) -> dict[str, Any]:
        assert endpoint == "negotiations"
        return {
            "items": [
                {
                    "id": "negotiation-old",
                    "resume": {"id": "resume-old"},
                    "vacancy": {"id": "vacancy"},
                    "state": {"id": "response"},
                }
            ],
            "pages": 1,
        }

    monkeypatch.setattr(driver, "call_api", fake_call_api)

    evidence = driver.find_application_evidence(
        resume_id="resume-current",
        vacancy_id="vacancy",
    )
    assert evidence["found"] is False
    assert evidence["matches"] == []


@pytest.mark.asyncio
async def test_application_service_observe_guard_fails_before_any_side_effect() -> None:
    class Driver:
        def fetch_vacancy(self, vacancy_id: str) -> dict[str, Any]:
            raise AssertionError("fetch must not run")

        def submit_application(self, **kwargs: Any) -> dict[str, Any]:
            raise AssertionError("submit must not run")

        def submit_application_with_test(self, **kwargs: Any) -> dict[str, Any]:
            raise AssertionError("test submit must not run")

    class Store:
        async def put_json(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("audit must not run")

    class Claims:
        async def acquire(self, **kwargs: Any) -> Any:
            raise AssertionError("claim must not run")

        async def transition(self, **kwargs: Any) -> Any:
            raise AssertionError("claim transition must not run")

    service = HHApplicationAuditService(
        driver=Driver(),
        store=Store(),
        claim_store=Claims(),  # type: ignore[arg-type]
        account_key="account",
        profile_id="profile",
    )
    with pytest.raises(HHExternalWriteForbidden):
        await service.apply(vacancy_id="v", resume_id="r", message="m")


def test_deprecated_live_maps_to_apply_but_does_not_create_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _parser().parse_args(["--live", "--resume-id", "resume"])
    assert _resolve_mode(args, None) is RuntimeMode.APPLY
    monkeypatch.delenv("CAREEROPS_HH_ALLOW_EXTERNAL_WRITES", raising=False)
    guard = HHExternalWriteGuard.from_env(RuntimeMode.APPLY)
    with pytest.raises(HHExternalWriteForbidden):
        guard.validate_write_capable_startup()


@pytest.mark.asyncio
async def test_deprecated_application_live_does_not_hide_invalid_mode() -> None:
    args = application_parser().parse_args(
        [
            "--mode",
            "shadow",
            "apply",
            "vacancy",
            "--resume-id",
            "resume",
            "--letter-file",
            "unused.txt",
            "--live",
        ]
    )
    with pytest.raises(ValueError, match="invalid HH runtime mode"):
        await run_application_cli(args, object())  # type: ignore[arg-type]
