from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class S3Settings:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str = "careerops-raw"
    region: str = "us-east-1"
    prefix: str = "_lab/hh"

    @classmethod
    def from_env(cls) -> "S3Settings":
        access_key = os.getenv("CAREEROPS_S3_ACCESS_KEY")
        secret_key = os.getenv("CAREEROPS_S3_SECRET_KEY")

        if not access_key:
            raise RuntimeError("CAREEROPS_S3_ACCESS_KEY is not set")
        if not secret_key:
            raise RuntimeError("CAREEROPS_S3_SECRET_KEY is not set")

        return cls(
            endpoint_url=os.getenv(
                "CAREEROPS_S3_ENDPOINT",
                "http://127.0.0.1:8333",
            ),
            access_key=access_key,
            secret_key=secret_key,
            bucket=os.getenv("CAREEROPS_S3_BUCKET", "careerops-raw"),
            region=os.getenv("CAREEROPS_S3_REGION", "us-east-1"),
            prefix=os.getenv("CAREEROPS_S3_PREFIX", "_lab/hh").strip("/"),
        )


@dataclass(frozen=True, slots=True)
class S3ObjectRef:
    bucket: str
    key: str
    sha256: str
    size_bytes: int

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


class S3JsonStore:
    """Minimal S3-compatible JSON writer for CareerOPS RAW/audit data."""

    def __init__(
        self,
        settings: S3Settings,
        *,
        client: BaseClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or boto3.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            region_name=settings.region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def _full_key(self, key: str) -> str:
        key = key.strip("/")
        prefix = self.settings.prefix.strip("/")
        return f"{prefix}/{key}" if prefix else key

    def put_json(self, key: str, payload: Any) -> S3ObjectRef:
        body = _json_bytes(payload)
        digest = hashlib.sha256(body).hexdigest()
        full_key = self._full_key(key)

        self.client.put_object(
            Bucket=self.settings.bucket,
            Key=full_key,
            Body=body,
            ContentType="application/json; charset=utf-8",
            Metadata={
                "sha256": digest,
                "producer": "careerops",
            },
        )

        return S3ObjectRef(
            bucket=self.settings.bucket,
            key=full_key,
            sha256=digest,
            size_bytes=len(body),
        )

    def get_json(self, key: str) -> Any:
        response = self.client.get_object(
            Bucket=self.settings.bucket,
            Key=self._full_key(key),
        )
        return json.loads(response["Body"].read().decode("utf-8"))

    def head(self, key: str) -> dict[str, Any]:
        return self.client.head_object(
            Bucket=self.settings.bucket,
            Key=self._full_key(key),
        )
