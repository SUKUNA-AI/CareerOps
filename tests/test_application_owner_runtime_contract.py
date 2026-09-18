from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
