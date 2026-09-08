from __future__ import annotations

import pytest
from pydantic import ValidationError

from careerops_processing.service.config import ProcessingRuntimeConfig


def _env() -> dict[str, str]:
    return {
        "CAREEROPS_PROCESSING_POSTGRES_DSN": "postgresql://careerops@10.42.0.1/careerops",
        "CAREEROPS_PROCESSING_S3_ENDPOINT_URL": "http://10.42.0.1:8333",
        "CAREEROPS_PROCESSING_S3_ACCESS_KEY": "test-access",
        "CAREEROPS_PROCESSING_S3_SECRET_KEY": "test-secret",
        "CAREEROPS_PROCESSING_NORMALIZED_BUCKET": "careerops-lake",
        "CAREEROPS_PROCESSING_ARTIFACTS_BUCKET": "careerops-artifacts",
        "CAREEROPS_PROCESSING_WORKER_ID": "core-processing-1",
    }


def test_processing_runtime_config_from_env() -> None:
    config = ProcessingRuntimeConfig.from_env(_env())

    assert config.health_host == "127.0.0.1"
    assert config.health_port == 18081
    assert config.s3_region == "us-east-1"
    assert config.artifacts_prefix == "processing"
    assert config.worker_id == "core-processing-1"
    assert config.worker_lease_seconds == 300
    assert config.worker_idle_sleep_seconds == 1.0


def test_processing_runtime_config_does_not_require_future_stage_dependencies() -> None:
    config = ProcessingRuntimeConfig.from_env(_env())

    assert not hasattr(config, "reranker_url")
    assert not hasattr(config, "matching_core_target")


def test_processing_runtime_config_rejects_bad_port() -> None:
    env = _env()
    env["CAREEROPS_PROCESSING_HEALTH_PORT"] = "not-an-int"

    with pytest.raises(ValueError, match="CAREEROPS_PROCESSING_HEALTH_PORT"):
        ProcessingRuntimeConfig.from_env(env)


def test_processing_runtime_config_is_strict() -> None:
    with pytest.raises(ValidationError):
        ProcessingRuntimeConfig(
            postgres_dsn="postgresql://careerops@10.42.0.1/careerops",
            s3_endpoint_url="http://10.42.0.1:8333",
            s3_access_key="test-access",
            s3_secret_key="test-secret",
            s3_region="us-east-1",
            normalized_bucket="careerops-lake",
            artifacts_bucket="careerops-artifacts",
            artifacts_prefix="processing",
            worker_id="core-processing-1",
            worker_lease_seconds=300,
            worker_idle_sleep_seconds=1.0,
            health_host="127.0.0.1",
            health_port="18081",  # type: ignore[arg-type]
        )


def test_processing_runtime_safe_summary_does_not_expose_secrets() -> None:
    config = ProcessingRuntimeConfig.from_env(_env())
    summary = config.safe_summary()

    assert summary["postgres_configured"] is True
    assert summary["s3_credentials_configured"] is True
    assert "postgres_dsn" not in summary
    assert "s3_access_key" not in summary
    assert "s3_secret_key" not in summary
    assert "test-access" not in summary.values()
    assert "test-secret" not in summary.values()
