from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path

from careerops_storage.s3 import S3Settings


@dataclass(frozen=True, slots=True)
class ApplicationServiceSettings:
    postgres_dsn: str
    hh_config_dir: Path
    s3: S3Settings
    worker_id: str
    poll_interval_seconds: float = 2.0
    lease_seconds: int = 120
    retry_after_seconds: int = 900
    reconcile_after_seconds: int = 120
    transport_timeout_seconds: float = 45.0
    account_limit_cooldown_seconds: int = 900
    health_host: str = "127.0.0.1"
    health_port: int = 18082
    cover_letter: str = ""

    @classmethod
    def from_env(cls) -> ApplicationServiceSettings:
        dsn = os.getenv("CAREEROPS_APPLICATION_POSTGRES_DSN") or os.getenv(
            "CAREEROPS_V2_POSTGRES_DSN"
        )
        if not dsn:
            raise RuntimeError(
                "CAREEROPS_APPLICATION_POSTGRES_DSN or CAREEROPS_V2_POSTGRES_DSN is required"
            )
        config_dir = Path(
            os.getenv(
                "CAREEROPS_APPLICATION_HH_CONFIG_DIR",
                "/var/lib/careerops/hh-applicant-tool",
            )
        )
        access_key = os.getenv("CAREEROPS_S3_ACCESS_KEY")
        secret_key = os.getenv("CAREEROPS_S3_SECRET_KEY")
        if not access_key or not secret_key:
            raise RuntimeError("CAREEROPS_S3_ACCESS_KEY and CAREEROPS_S3_SECRET_KEY are required")
        s3 = S3Settings(
            endpoint_url=os.getenv("CAREEROPS_S3_ENDPOINT", "http://127.0.0.1:8333"),
            access_key=access_key,
            secret_key=secret_key,
            bucket=os.getenv("CAREEROPS_APPLICATION_S3_BUCKET", "careerops-artifacts"),
            region=os.getenv("CAREEROPS_S3_REGION", "us-east-1"),
            prefix=os.getenv(
                "CAREEROPS_APPLICATION_S3_PREFIX",
                "application-owner",
            ).strip("/"),
        )
        lease_seconds = _int_env("CAREEROPS_APPLICATION_LEASE_SECONDS", 120)
        transport_timeout_seconds = _float_env(
            "CAREEROPS_APPLICATION_TRANSPORT_TIMEOUT_SECONDS",
            45.0,
        )
        if transport_timeout_seconds >= lease_seconds:
            raise ValueError(
                "CAREEROPS_APPLICATION_TRANSPORT_TIMEOUT_SECONDS must be smaller than "
                "CAREEROPS_APPLICATION_LEASE_SECONDS"
            )
        return cls(
            postgres_dsn=dsn,
            hh_config_dir=config_dir,
            s3=s3,
            worker_id=os.getenv(
                "CAREEROPS_APPLICATION_WORKER_ID",
                f"{socket.gethostname()}-application-owner",
            ),
            poll_interval_seconds=_float_env("CAREEROPS_APPLICATION_POLL_SECONDS", 2.0),
            lease_seconds=lease_seconds,
            retry_after_seconds=_int_env("CAREEROPS_APPLICATION_RETRY_SECONDS", 900),
            reconcile_after_seconds=_int_env(
                "CAREEROPS_APPLICATION_RECONCILE_SECONDS",
                120,
            ),
            transport_timeout_seconds=transport_timeout_seconds,
            account_limit_cooldown_seconds=_int_env(
                "CAREEROPS_APPLICATION_ACCOUNT_LIMIT_COOLDOWN_SECONDS",
                900,
            ),
            health_host=os.getenv("CAREEROPS_APPLICATION_HEALTH_HOST", "127.0.0.1"),
            health_port=_int_env("CAREEROPS_APPLICATION_HEALTH_PORT", 18082),
            cover_letter=_cover_letter_from_env(),
        )


def _int_env(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be > 0")
    return value


def _float_env(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be > 0")
    return value


def _cover_letter_from_env() -> str:
    inline = os.getenv("CAREEROPS_APPLICATION_COVER_LETTER")
    if inline is not None:
        return inline
    path = os.getenv("CAREEROPS_APPLICATION_COVER_LETTER_FILE")
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8").strip()
