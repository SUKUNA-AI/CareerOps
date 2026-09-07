from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from typing import Any

import pytest

from careerops_integrations.hh.driver import HHApplicantToolCLI


def test_driver_is_structurally_read_only() -> None:
    signature = inspect.signature(HHApplicantToolCLI)
    assert "external_write_guard" not in signature.parameters
    assert all(
        parameter.kind is not inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    assert not hasattr(HHApplicantToolCLI, "submit_application")
    assert not hasattr(HHApplicantToolCLI, "submit_application_with_test")
    assert not hasattr(HHApplicantToolCLI, "find_application_evidence")


def test_driver_delegates_profile_auth_to_hh_applicant_tool() -> None:
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


def test_call_api_constructs_get_only_command(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    driver = HHApplicantToolCLI(
        config_dir=Path("config"),
        profile="profile",
        python_executable="python",
    )

    result = driver.call_api(
        "/vacancies",
        params={"page": 0, "professional_role": (1, 2)},
    )

    assert result == {"ok": True}
    command = captured["command"]
    assert command[-4:] == ["vacancies", "page=0", "professional_role=1", "professional_role=2"]
    assert "--method" not in command
    assert "POST" not in command


def test_call_api_rejects_empty_endpoint() -> None:
    driver = HHApplicantToolCLI(config_dir=Path("config"), profile="profile")
    with pytest.raises(ValueError, match="endpoint must not be empty"):
        driver.call_api("   ")


def test_driver_rejects_legacy_write_capability_argument() -> None:
    with pytest.raises(TypeError):
        HHApplicantToolCLI(  # type: ignore[call-arg]
            config_dir=Path("config"),
            profile="profile",
            external_write_guard=object(),
        )
