from pathlib import Path

import pytest

from careerops_application.service.config import ApplicationServiceSettings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CAREEROPS_APPLICATION_POSTGRES_DSN",
        "postgresql://careerops:careerops@127.0.0.1:5432/careerops",
    )
    monkeypatch.setenv("CAREEROPS_S3_ACCESS_KEY", "test-access")
    monkeypatch.setenv("CAREEROPS_S3_SECRET_KEY", "test-secret")


def test_application_owner_compose_keeps_vendor_state_on_explicit_rw_mount() -> None:
    compose = (
        PROJECT_ROOT / "infra" / "compose" / "application-owner" / "compose.yml"
    ).read_text(encoding="utf-8")
    assert "read_only: true" in compose
    assert "/etc/careerops/hh-applicant-tool:/var/lib/careerops/hh-applicant-tool:rw" in compose
    assert "careerops-application-owner" in compose


def test_application_owner_image_installs_vendor_as_dependency_not_source_package() -> None:
    dockerfile = (
        PROJECT_ROOT / "infra" / "compose" / "application-owner" / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert "COPY hh-applicant-tool /vendor/hh-applicant-tool" in dockerfile
    assert "pip install --no-cache-dir . /vendor/hh-applicant-tool" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "careerops_application"]' in dockerfile


def test_application_owner_external_writes_are_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _required_env(monkeypatch)
    monkeypatch.delenv("CAREEROPS_APPLICATION_ALLOW_EXTERNAL_WRITES", raising=False)

    settings = ApplicationServiceSettings.from_env()

    assert settings.allow_external_writes is False


def test_application_owner_external_writes_require_explicit_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _required_env(monkeypatch)
    monkeypatch.setenv("CAREEROPS_APPLICATION_ALLOW_EXTERNAL_WRITES", "true")

    settings = ApplicationServiceSettings.from_env()

    assert settings.allow_external_writes is True


def test_application_owner_rejects_ambiguous_external_write_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _required_env(monkeypatch)
    monkeypatch.setenv("CAREEROPS_APPLICATION_ALLOW_EXTERNAL_WRITES", "maybe")

    with pytest.raises(ValueError, match="must be an explicit boolean"):
        ApplicationServiceSettings.from_env()
