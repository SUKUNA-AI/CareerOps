"""Runtime configuration for the standalone careerops-processing service."""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProcessingRuntimeConfig(BaseModel):
    """Environment-backed runtime configuration with no embedded credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    postgres_dsn: str = Field(min_length=1)
    s3_endpoint_url: str = Field(min_length=1)
    normalized_bucket: str = Field(min_length=1)
    artifacts_bucket: str = Field(min_length=1)
    reranker_url: str = Field(min_length=1)
    matching_core_target: str = Field(min_length=1)
    worker_id: str = Field(min_length=1)
    health_host: str = Field(min_length=1)
    health_port: int = Field(ge=1, le=65535)

    @field_validator("s3_endpoint_url", "reranker_url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("service URL must start with http:// or https://")
        return value

    @field_validator("normalized_bucket", "artifacts_bucket")
    @classmethod
    def validate_bucket_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("bucket name must not be blank")
        return stripped

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> ProcessingRuntimeConfig:
        env = os.environ if environ is None else environ

        def required(name: str) -> str:
            value = env.get(name, "").strip()
            if not value:
                raise ValueError(f"missing required environment variable: {name}")
            return value

        raw_port = env.get("CAREEROPS_PROCESSING_HEALTH_PORT", "18081").strip()
        try:
            health_port = int(raw_port)
        except ValueError as exc:
            raise ValueError("CAREEROPS_PROCESSING_HEALTH_PORT must be an integer") from exc

        return cls(
            postgres_dsn=required("CAREEROPS_PROCESSING_POSTGRES_DSN"),
            s3_endpoint_url=required("CAREEROPS_PROCESSING_S3_ENDPOINT_URL"),
            normalized_bucket=required("CAREEROPS_PROCESSING_NORMALIZED_BUCKET"),
            artifacts_bucket=required("CAREEROPS_PROCESSING_ARTIFACTS_BUCKET"),
            reranker_url=required("CAREEROPS_PROCESSING_RERANKER_URL"),
            matching_core_target=required("CAREEROPS_PROCESSING_MATCHING_CORE_TARGET"),
            worker_id=env.get("CAREEROPS_PROCESSING_WORKER_ID", socket.gethostname()).strip()
            or socket.gethostname(),
            health_host=env.get("CAREEROPS_PROCESSING_HEALTH_HOST", "127.0.0.1").strip()
            or "127.0.0.1",
            health_port=health_port,
        )

    def safe_summary(self) -> dict[str, object]:
        """Return non-secret runtime wiring for diagnostics."""

        return {
            "postgres_configured": bool(self.postgres_dsn),
            "s3_endpoint_url": self.s3_endpoint_url,
            "normalized_bucket": self.normalized_bucket,
            "artifacts_bucket": self.artifacts_bucket,
            "reranker_url": self.reranker_url,
            "matching_core_target": self.matching_core_target,
            "worker_id": self.worker_id,
            "health_host": self.health_host,
            "health_port": self.health_port,
        }
