"""Настройки запуска отдельного сервиса careerops-processing"""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProcessingRuntimeConfig(BaseModel):
    """Настройки Processing runtime до P2-05 включительно"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    postgres_dsn: str = Field(min_length=1)
    s3_endpoint_url: str = Field(min_length=1)
    s3_access_key: str = Field(min_length=1)
    s3_secret_key: str = Field(min_length=1)
    s3_region: str = Field(min_length=1)
    normalized_bucket: str = Field(min_length=1)
    artifacts_bucket: str = Field(min_length=1)
    artifacts_prefix: str = Field(min_length=1)
    reranker_endpoint_url: str = Field(min_length=1)
    reranker_timeout_seconds: float = Field(gt=0)
    reranker_unavailable_delay_seconds: float = Field(gt=0)
    worker_id: str = Field(min_length=1)
    worker_lease_seconds: int = Field(ge=3)
    worker_idle_sleep_seconds: float = Field(gt=0)
    health_host: str = Field(min_length=1)
    health_port: int = Field(ge=1, le=65535)

    @field_validator("s3_endpoint_url", "reranker_endpoint_url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("service URL должен начинаться с http:// или https://")
        return value

    @field_validator("normalized_bucket", "artifacts_bucket")
    @classmethod
    def validate_bucket_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("bucket name не должен быть пустым")
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
                raise ValueError(f"не задана обязательная переменная окружения: {name}")
            return value

        def integer(name: str, default: str) -> int:
            raw = env.get(name, default).strip()
            try:
                return int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} должен быть integer") from exc

        def floating(name: str, default: str) -> float:
            raw = env.get(name, default).strip()
            try:
                return float(raw)
            except ValueError as exc:
                raise ValueError(f"{name} должен быть number") from exc

        return cls(
            postgres_dsn=required("CAREEROPS_PROCESSING_POSTGRES_DSN"),
            s3_endpoint_url=required("CAREEROPS_PROCESSING_S3_ENDPOINT_URL"),
            s3_access_key=required("CAREEROPS_PROCESSING_S3_ACCESS_KEY"),
            s3_secret_key=required("CAREEROPS_PROCESSING_S3_SECRET_KEY"),
            s3_region=env.get("CAREEROPS_PROCESSING_S3_REGION", "us-east-1").strip()
            or "us-east-1",
            normalized_bucket=required("CAREEROPS_PROCESSING_NORMALIZED_BUCKET"),
            artifacts_bucket=required("CAREEROPS_PROCESSING_ARTIFACTS_BUCKET"),
            artifacts_prefix=env.get(
                "CAREEROPS_PROCESSING_ARTIFACTS_PREFIX",
                "processing",
            ).strip("/")
            or "processing",
            reranker_endpoint_url=required("CAREEROPS_PROCESSING_RERANKER_URL"),
            reranker_timeout_seconds=floating(
                "CAREEROPS_PROCESSING_RERANKER_TIMEOUT_SECONDS",
                "60",
            ),
            reranker_unavailable_delay_seconds=floating(
                "CAREEROPS_PROCESSING_RERANKER_UNAVAILABLE_DELAY_SECONDS",
                "120",
            ),
            worker_id=env.get("CAREEROPS_PROCESSING_WORKER_ID", socket.gethostname()).strip()
            or socket.gethostname(),
            worker_lease_seconds=integer(
                "CAREEROPS_PROCESSING_WORKER_LEASE_SECONDS",
                "300",
            ),
            worker_idle_sleep_seconds=floating(
                "CAREEROPS_PROCESSING_WORKER_IDLE_SLEEP_SECONDS",
                "1.0",
            ),
            health_host=env.get("CAREEROPS_PROCESSING_HEALTH_HOST", "127.0.0.1").strip()
            or "127.0.0.1",
            health_port=integer("CAREEROPS_PROCESSING_HEALTH_PORT", "18081"),
        )

    def safe_summary(self) -> dict[str, object]:
        """Возвращает настройки подключения без секретов"""

        return {
            "postgres_configured": bool(self.postgres_dsn),
            "s3_endpoint_url": self.s3_endpoint_url,
            "s3_credentials_configured": bool(self.s3_access_key and self.s3_secret_key),
            "s3_region": self.s3_region,
            "normalized_bucket": self.normalized_bucket,
            "artifacts_bucket": self.artifacts_bucket,
            "artifacts_prefix": self.artifacts_prefix,
            "reranker_endpoint_url": self.reranker_endpoint_url,
            "reranker_timeout_seconds": self.reranker_timeout_seconds,
            "reranker_unavailable_delay_seconds": self.reranker_unavailable_delay_seconds,
            "worker_id": self.worker_id,
            "worker_lease_seconds": self.worker_lease_seconds,
            "worker_idle_sleep_seconds": self.worker_idle_sleep_seconds,
            "health_host": self.health_host,
            "health_port": self.health_port,
        }
