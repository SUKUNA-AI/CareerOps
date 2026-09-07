"""Content-addressed immutable artifact storage для Processing v2"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any, cast

import aioboto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from pydantic import BaseModel

from careerops_processing.contracts.artifacts import (
    ProcessingArtifactKind,
    ProcessingArtifactRef,
)

_SAFE_SEGMENT = re.compile(r"^[a-zA-Z0-9._-]+$")


class ProcessingArtifactIntegrityError(RuntimeError):
    """Ошибка нарушения content-addressed artifact invariant"""


@dataclass(frozen=True, slots=True)
class ProcessingArtifactStoreSettings:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str = "careerops-artifacts"
    region: str = "us-east-1"
    prefix: str = "processing"


class ProcessingArtifactStore:
    """Пишет immutable JSON bundles по SHA-256 content address"""

    def __init__(
        self,
        settings: ProcessingArtifactStoreSettings,
        *,
        client: Any | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self._client_context: Any | None = None

    async def __aenter__(self) -> ProcessingArtifactStore:
        if self.client is None:
            session = aioboto3.Session()
            self._client_context = session.client(
                "s3",
                endpoint_url=self.settings.endpoint_url,
                aws_access_key_id=self.settings.access_key,
                aws_secret_access_key=self.settings.secret_key,
                region_name=self.settings.region,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
            self.client = await self._client_context.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._client_context is not None:
            await self._client_context.__aexit__(exc_type, exc_value, traceback)
            self._client_context = None
            self.client = None

    def _require_client(self) -> Any:
        if self.client is None:
            raise RuntimeError("ProcessingArtifactStore требует async context manager")
        return self.client

    @staticmethod
    def _canonical_bytes(payload: BaseModel | Mapping[str, Any]) -> bytes:
        if isinstance(payload, BaseModel):
            value: object = payload.model_dump(mode="json")
        else:
            value = dict(payload)
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @staticmethod
    def _safe_segment(value: str, field_name: str) -> str:
        if not _SAFE_SEGMENT.fullmatch(value):
            raise ValueError(f"{field_name} содержит недопустимые символы: {value!r}")
        return value

    def _key(
        self,
        *,
        kind: ProcessingArtifactKind,
        schema_version: str,
        sha256: str,
    ) -> str:
        kind_segment = self._safe_segment(kind.value, "artifact kind")
        schema_segment = self._safe_segment(schema_version, "schema version")
        prefix = self.settings.prefix.strip("/")
        relative = f"{kind_segment}/{schema_segment}/{sha256[:2]}/{sha256}.json"
        return f"{prefix}/{relative}" if prefix else relative

    @staticmethod
    def _is_missing(exc: ClientError) -> bool:
        error = exc.response.get("Error", {})
        code = str(error.get("Code", ""))
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code in {"404", "NoSuchKey", "NotFound"} or status == 404

    async def _read_existing(self, key: str) -> tuple[bytes, dict[str, str]] | None:
        client = self._require_client()
        try:
            response = cast(
                dict[str, Any],
                await client.get_object(Bucket=self.settings.bucket, Key=key),
            )
        except ClientError as exc:
            if self._is_missing(exc):
                return None
            raise
        body = cast(bytes, await response["Body"].read())
        metadata = response.get("Metadata", {})
        if not isinstance(metadata, dict):
            raise ProcessingArtifactIntegrityError("artifact S3 metadata имеет неверный тип")
        return body, {str(k): str(v) for k, v in metadata.items()}

    @staticmethod
    def _verify_bytes(
        *,
        body: bytes,
        metadata: Mapping[str, str],
        expected_sha256: str,
        expected_size: int,
    ) -> None:
        digest = hashlib.sha256(body).hexdigest()
        if digest != expected_sha256 or len(body) != expected_size:
            raise ProcessingArtifactIntegrityError(
                "content-addressed artifact bytes не совпадают с ожидаемым digest"
            )
        stored_digest = metadata.get("sha256")
        if stored_digest != expected_sha256:
            raise ProcessingArtifactIntegrityError("artifact metadata sha256 не совпадает")

    async def put_contract(
        self,
        *,
        kind: ProcessingArtifactKind,
        schema_version: str,
        payload: BaseModel | Mapping[str, Any],
    ) -> ProcessingArtifactRef:
        body = self._canonical_bytes(payload)
        digest = hashlib.sha256(body).hexdigest()
        key = self._key(kind=kind, schema_version=schema_version, sha256=digest)

        existing = await self._read_existing(key)
        if existing is None:
            client = self._require_client()
            await client.put_object(
                Bucket=self.settings.bucket,
                Key=key,
                Body=body,
                ContentType="application/json; charset=utf-8",
                Metadata={
                    "sha256": digest,
                    "artifact-kind": kind.value,
                    "schema-version": schema_version,
                    "producer": "careerops-processing",
                },
            )
            existing = await self._read_existing(key)
            if existing is None:
                raise ProcessingArtifactIntegrityError(
                    "artifact отсутствует после успешного put_object"
                )

        existing_body, metadata = existing
        self._verify_bytes(
            body=existing_body,
            metadata=metadata,
            expected_sha256=digest,
            expected_size=len(body),
        )
        return ProcessingArtifactRef(
            kind=kind,
            schema_version=schema_version,
            uri=f"s3://{self.settings.bucket}/{key}",
            sha256=digest,
            size_bytes=len(body),
        )

    async def get_contract_json(self, ref: ProcessingArtifactRef) -> dict[str, Any]:
        prefix = f"s3://{self.settings.bucket}/"
        if not ref.uri.startswith(prefix):
            raise ValueError("artifact ref указывает на другой bucket")
        key = ref.uri[len(prefix) :]
        existing = await self._read_existing(key)
        if existing is None:
            raise FileNotFoundError(ref.uri)
        body, metadata = existing
        self._verify_bytes(
            body=body,
            metadata=metadata,
            expected_sha256=ref.sha256,
            expected_size=ref.size_bytes,
        )
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ProcessingArtifactIntegrityError("artifact root должен быть JSON object")
        return value
