from __future__ import annotations

import pytest
from pydantic import ValidationError

from careerops_processing.service.config import ProcessingRuntimeConfig


def _env() -> dict[str, str]:
    return {
        "CAREEROPS_PROCESSING_POSTGRES_DSN": "postgresql://careerops@10.42.0.1/careerops",
        "CAREEROPS_PROCESSING_S3_ENDPOINT_URL": "http://10.42.0.1:8333",
        "CAREEROPS_PROCESSING_NORMALIZED_BUCKET": "careerops-lake",
        "CAREEROPS_PROCESSING_ARTIFACTS_BUCKET": "careerops-artifacts",
        "CAREEROPS_PROCESSING_RERANKER_URL": "http://10.42.0.1:18082",
        "CAREEROPS_PROCESSING_MATCHING_CORE_TARGET": "127.0.0.1:50051",
        "CAREEROPS_PROCESSING_WORKER_ID": "core-processing-1",
    }


def test_processing_runtime_config_from_env() -> None:
    config = ProcessingRuntimeConfig.from_env(_env())

    assert config.health_host == "127.0.0.1"
    assert config.health_port == 18081
    assert config.matching_core_target == "127.0.0.1:50051"
    assert config.reranker_url == "http://10.42.0.1:18082"


def test_processing_runtime_config_requires_semantic_dependencies() -> None:
    env = _env()
    del env["CAREEROPS_PROCESSING_RERANKER_URL"]

    with pytest.raises(ValueError, match="CAREEROPS_PROCESSING_RERANKER_URL"):
        ProcessingRuntimeConfig.from_env(env)


def test_processing_runtime_config_rejects_bad_port() -> None:
    env = _env()
    env["CAREEROPS_PROCESSING_HEALTH_PORT"] = "not-an-int"

    with pytest.raises(ValueError, match="HEALTH_PORT must be an integer"):
        ProcessingRuntimeConfig.from_env(env)


def test_processing_runtime_config_is_strict() -> None:
    with pytest.raises(ValidationError):
        ProcessingRuntimeConfig(
            postgres_dsn="postgresql://careerops@10.42.0.1/careerops",
            s3_endpoint_url="http://10.42.0.1:8333",
            normalized_bucket="careerops-lake",
            artifacts_bucket="careerops-artifacts",
            reranker_url="http://10.42.0.1:18082",
            matching_core_target="127.0.0.1:50051",
            worker_id="core-processing-1",
            health_host="127.0.0.1",
            health_port="18081",  # type: ignore[arg-type]
        )


def test_processing_runtime_safe_summary_does_not_expose_dsn() -> None:
    config = ProcessingRuntimeConfig.from_env(_env())
    summary = config.safe_summary()

    assert summary["postgres_configured"] is True
    assert "postgres_dsn" not in summary
