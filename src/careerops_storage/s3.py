"""Асинхронное JSON-хранилище SeaweedFS S3 для RAW и аудита CareerOPS"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, cast
from urllib.parse import urlsplit

import aioboto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]


def _json_bytes(payload: Any) -> bytes:
    """Детерминированно сериализует JSON для хранения и вычисления хеша"""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _client_config() -> Config:
    """Возвращает общую path-style конфигурацию S3-клиента для SeaweedFS"""

    return Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
        retries={"max_attempts": 3, "mode": "standard"},
    )


def _normalize_collected_at(value: datetime, field_name: str) -> datetime:
    """Проверяет timezone-aware время наблюдения и приводит его к UTC"""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _parse_collected_at_metadata(value: Any, full_key: str) -> datetime | None:
    """Строго разбирает S3 metadata collected_at без запасного пути"""

    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"invalid S3 collected_at metadata for {full_key!r}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"invalid S3 collected_at metadata for {full_key!r}: {value!r}"
        ) from exc
    return _normalize_collected_at(
        parsed,
        f"S3 collected_at metadata for {full_key!r}",
    )


@dataclass(frozen=True, slots=True)
class S3Settings:
    """Настройки подключения и пространства ключей RAW bucket CareerOPS"""

    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str = "careerops-raw"
    region: str = "us-east-1"
    prefix: str = "_lab/hh"

    @classmethod
    def from_env(cls) -> S3Settings:
        """Загружает настройки S3 из env без значений по умолчанию для секретов"""

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
    """Provenance одного неизменяемого JSON-объекта в S3"""

    bucket: str
    key: str
    sha256: str
    size_bytes: int
    collected_at: datetime | None = None
    last_modified: datetime | None = None

    @property
    def uri(self) -> str:
        """Возвращает канонический S3 URI объекта"""

        return f"s3://{self.bucket}/{self.key}"


class _S3KeySpace:
    """Централизует безопасную нормализацию ключей асинхронного S3-хранилища"""

    settings: S3Settings

    def _full_key(self, key: str) -> str:
        """Преобразует относительный ключ, полный ключ или S3 URI в ключ bucket"""

        value = key.strip()
        is_uri = value.startswith("s3://")

        if is_uri:
            parsed = urlsplit(value)
            if parsed.query or parsed.fragment:
                raise ValueError(f"S3 URI must not contain query or fragment: {key!r}")
            if parsed.netloc != self.settings.bucket:
                raise ValueError(
                    "S3 URI bucket does not match configured bucket: "
                    f"uri={parsed.netloc!r}, configured={self.settings.bucket!r}"
                )
            value = parsed.path

        value = value.strip("/")
        prefix = self.settings.prefix.strip("/")

        if is_uri and prefix and value != prefix and not value.startswith(prefix + "/"):
            raise ValueError(
                f"S3 URI key {value!r} is outside configured prefix {prefix!r}"
            )
        if is_uri or not prefix:
            return value
        if value == prefix or value.startswith(prefix + "/"):
            return value
        return f"{prefix}/{value}" if value else prefix

    def relative_key(self, key: str) -> str:
        """Возвращает ключ относительно настроенного prefix для любой допустимой формы"""

        full_key = self._full_key(key)
        prefix = self.settings.prefix.strip("/")

        if not prefix:
            return full_key
        if full_key == prefix:
            return ""
        if full_key.startswith(prefix + "/"):
            return full_key[len(prefix) + 1 :]
        raise ValueError(f"S3 key {full_key!r} is outside configured prefix {prefix!r}")

    def _relative_from_full(self, full_key: str) -> str | None:
        """Преобразует полный ключ из listing в относительный или отбрасывает чужой prefix"""

        configured_prefix = self.settings.prefix.strip("/")
        if not configured_prefix:
            return full_key
        expected = configured_prefix + "/"
        if full_key.startswith(expected):
            return full_key[len(expected) :]
        if full_key == configured_prefix:
            return ""
        return None

    def _decode_object(
        self,
        *,
        body: bytes,
        response: dict[str, Any],
        full_key: str,
    ) -> tuple[Any, S3ObjectRef]:
        """Декодирует JSON и проверяет хеш и временной provenance объекта"""

        payload = json.loads(body.decode("utf-8"))
        digest = hashlib.sha256(body).hexdigest()
        metadata = response.get("Metadata", {})
        if not isinstance(metadata, dict):
            raise TypeError(f"invalid S3 Metadata for {full_key!r}")
        stored_digest = metadata.get("sha256")
        if stored_digest is not None and stored_digest.lower() != digest:
            raise ValueError(
                f"S3 sha256 metadata mismatch for s3://{self.settings.bucket}/{full_key}"
            )

        collected_at = _parse_collected_at_metadata(
            metadata.get("collected_at"),
            full_key,
        )
        last_modified = response.get("LastModified")
        if last_modified is not None:
            if not isinstance(last_modified, datetime):
                raise TypeError(f"invalid S3 LastModified for {full_key!r}")
            if last_modified.tzinfo is None or last_modified.utcoffset() is None:
                raise ValueError(f"timezone-naive S3 LastModified for {full_key!r}")
            last_modified = last_modified.astimezone(UTC)

        return (
            payload,
            S3ObjectRef(
                bucket=self.settings.bucket,
                key=full_key,
                sha256=digest,
                size_bytes=len(body),
                collected_at=collected_at,
                last_modified=last_modified,
            ),
        )


class S3JsonStore(_S3KeySpace):
    """Читает и пишет JSON CareerOPS через асинхронный клиент aioboto3"""

    def __init__(self, settings: S3Settings, *, client: Any | None = None) -> None:
        """Готовит async client с возможностью подставить внешний клиент"""

        self.settings = settings
        self.client = client
        self._client_context: Any | None = None

    async def __aenter__(self) -> S3JsonStore:
        """Открывает принадлежащий хранилищу S3-клиент aioboto3"""

        if self.client is None:
            session = aioboto3.Session()
            self._client_context = session.client(
                "s3",
                endpoint_url=self.settings.endpoint_url,
                aws_access_key_id=self.settings.access_key,
                aws_secret_access_key=self.settings.secret_key,
                region_name=self.settings.region,
                config=_client_config(),
            )
            self.client = await self._client_context.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Закрывает созданный хранилищем клиент и не трогает подставленный извне"""

        if self._client_context is not None:
            await self._client_context.__aexit__(exc_type, exc_value, traceback)
            self._client_context = None
            self.client = None

    def _require_client(self) -> Any:
        """Возвращает активный async client или сообщает об отсутствии context manager"""

        if self.client is None:
            raise RuntimeError("S3JsonStore must be used as an async context manager")
        return self.client

    async def put_json(
        self,
        key: str,
        payload: Any,
        *,
        collected_at: datetime | None = None,
    ) -> S3ObjectRef:
        """Пишет JSON без смысловых изменений вместе с checksum и временем наблюдения"""

        client = self._require_client()
        body = _json_bytes(payload)
        digest = hashlib.sha256(body).hexdigest()
        full_key = self._full_key(key)
        observed_at = _normalize_collected_at(
            collected_at or datetime.now(UTC),
            "collected_at",
        )
        await client.put_object(
            Bucket=self.settings.bucket,
            Key=full_key,
            Body=body,
            ContentType="application/json; charset=utf-8",
            Metadata={
                "sha256": digest,
                "producer": "careerops",
                "collected_at": observed_at.isoformat(),
            },
        )
        return S3ObjectRef(
            bucket=self.settings.bucket,
            key=full_key,
            sha256=digest,
            size_bytes=len(body),
            collected_at=observed_at,
        )

    async def get_json(self, key: str) -> Any:
        """Асинхронно читает и возвращает одно JSON-значение"""

        payload, _ = await self.get_json_with_metadata(key)
        return payload

    async def head(self, key: str) -> dict[str, Any]:
        """Асинхронно возвращает исходные S3 metadata объекта"""

        client = self._require_client()
        return cast(
            dict[str, Any],
            await client.head_object(
                Bucket=self.settings.bucket,
                Key=self._full_key(key),
            ),
        )

    async def iter_keys(self, prefix: str = "") -> AsyncIterator[str]:
        """Итерирует относительные ключи через асинхронный paginated S3 listing"""

        client = self._require_client()
        full_prefix = self._full_key(prefix)
        paginator = client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(
            Bucket=self.settings.bucket,
            Prefix=full_prefix,
        ):
            for item in page.get("Contents", []):
                relative = self._relative_from_full(str(item["Key"]))
                if relative is not None:
                    yield relative

    async def get_json_with_metadata(self, key: str) -> tuple[Any, S3ObjectRef]:
        """Асинхронно читает JSON и проверяет checksum и S3 metadata"""

        client = self._require_client()
        full_key = self._full_key(key)
        response = cast(
            dict[str, Any],
            await client.get_object(Bucket=self.settings.bucket, Key=full_key),
        )
        body = cast(bytes, await response["Body"].read())
        return self._decode_object(body=body, response=response, full_key=full_key)
