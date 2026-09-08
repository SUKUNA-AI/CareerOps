"""Immutable HH RAW contract для публикации в SeaweedFS

Object key содержит source identity и observation UUID, созданный caller-ом
При retry публикации уже загруженной observation caller должен повторно использовать тот же UUID
Новый source fetch является новой observation и получает новый UUID
Collision одного key с другим content завершается ошибкой вместо перезаписи предыдущей observation

Create-only conditional PUT semantics SeaweedFS здесь намеренно не предполагаются
Уникальность observation ID предотвращает штатные writer races, а existence/hash checks
делают retry одной observation idempotent и обнаруживают случайное повторное использование key
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import quote
from uuid import UUID

from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from careerops_storage.s3 import S3JsonStore, S3ObjectRef


class RawObjectCollisionError(RuntimeError):
    """Отклоняет reuse immutable RAW key с другим source content"""


class RawWriteVerificationError(RuntimeError):
    """Ошибка проверки RAW write после сохранения в S3"""


class HHRawObjectKind(StrEnum):
    """Семейства внешних HH payload, которые хранятся в careerops-raw"""

    SEARCH_PAGE = "search-page"
    VACANCY = "vacancy"
    RESUME_LIST_PAGE = "resume-list-page"
    RESUME = "resume"


@dataclass(frozen=True, slots=True)
class HHRawContext:
    """Стабильный provenance одной точной HH source observation"""

    account_key: str
    profile_key: str
    observed_at: datetime
    observation_id: UUID

    def __post_init__(self) -> None:
        if not self.account_key.strip():
            raise ValueError("account_key must not be empty")
        if not self.profile_key.strip():
            raise ValueError("profile_key must not be empty")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class HHRawObject:
    """Опубликованный RAW object с kind и observation identity"""

    kind: HHRawObjectKind
    observation_id: UUID
    ref: S3ObjectRef


def _segment(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("RAW key segment must not be empty")
    return quote(normalized, safe="-_.~")


def _observation_stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _canonical_json_bytes(payload: Any) -> bytes:
    """Проверяет JSON source data и сериализует их так же, как S3JsonStore"""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _is_missing(exc: ClientError) -> bool:
    error = exc.response.get("Error", {})
    code = str(error.get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


class HHRawPublisher:
    """Публикует точные HH JSON bodies под immutable observation keys"""

    def __init__(self, store: S3JsonStore) -> None:
        self._store = store

    def _key(
        self,
        *,
        context: HHRawContext,
        kind: HHRawObjectKind,
        identity_parts: tuple[str, ...],
    ) -> str:
        observed = context.observed_at.astimezone(UTC)
        parts = [
            "v2",
            kind.value,
            f"date={observed.date().isoformat()}",
            f"account={_segment(context.account_key)}",
            f"profile={_segment(context.profile_key)}",
            *identity_parts,
            f"observed_at={_observation_stamp(observed)}",
            f"observation_id={context.observation_id}.json",
        ]
        return "/".join(parts)

    async def _publish(
        self,
        *,
        context: HHRawContext,
        kind: HHRawObjectKind,
        identity_parts: tuple[str, ...],
        payload: Any,
    ) -> HHRawObject:
        body = _canonical_json_bytes(payload)
        digest = hashlib.sha256(body).hexdigest()
        key = self._key(
            context=context,
            kind=kind,
            identity_parts=identity_parts,
        )

        try:
            existing_payload, existing_ref = await self._store.get_json_with_metadata(key)
        except ClientError as exc:
            if not _is_missing(exc):
                raise
        else:
            existing_digest = hashlib.sha256(
                _canonical_json_bytes(existing_payload)
            ).hexdigest()
            if existing_ref.sha256 != digest or existing_digest != digest:
                raise RawObjectCollisionError(
                    "immutable RAW key already exists with different content: "
                    f"{existing_ref.uri}"
                )
            return HHRawObject(
                kind=kind,
                observation_id=context.observation_id,
                ref=existing_ref,
            )

        ref = await self._store.put_json(
            key,
            payload,
            collected_at=context.observed_at,
        )
        if ref.sha256 != digest or ref.size_bytes != len(body):
            raise RawWriteVerificationError(
                f"RAW writer returned inconsistent checksum/size for {ref.uri}"
            )

        persisted = await self._store.head(key)
        metadata = persisted.get("Metadata", {})
        if not isinstance(metadata, dict):
            raise RawWriteVerificationError(f"RAW object has invalid metadata: {ref.uri}")
        persisted_digest = str(metadata.get("sha256", "")).lower()
        persisted_size = persisted.get("ContentLength")
        if persisted_digest != digest or persisted_size != len(body):
            raise RawWriteVerificationError(
                f"RAW object verification failed after PUT: {ref.uri}"
            )

        return HHRawObject(
            kind=kind,
            observation_id=context.observation_id,
            ref=ref,
        )

    async def publish_search_page(
        self,
        *,
        context: HHRawContext,
        query_key: str,
        page: int,
        payload: Any,
    ) -> HHRawObject:
        """Публикует один точный page поиска вакансий"""

        if page < 0:
            raise ValueError("page must be >= 0")
        return await self._publish(
            context=context,
            kind=HHRawObjectKind.SEARCH_PAGE,
            identity_parts=(
                f"query={_segment(query_key)}",
                f"page={page}",
            ),
            payload=payload,
        )

    async def publish_vacancy(
        self,
        *,
        context: HHRawContext,
        vacancy_id: str,
        payload: Any,
    ) -> HHRawObject:
        """Публикует один точный full vacancy response"""

        return await self._publish(
            context=context,
            kind=HHRawObjectKind.VACANCY,
            identity_parts=(f"vacancy_id={_segment(vacancy_id)}",),
            payload=payload,
        )

    async def publish_resume_list_page(
        self,
        *,
        context: HHRawContext,
        page: int,
        payload: Any,
    ) -> HHRawObject:
        """Публикует один точный page /resumes/mine"""

        if page < 0:
            raise ValueError("page must be >= 0")
        return await self._publish(
            context=context,
            kind=HHRawObjectKind.RESUME_LIST_PAGE,
            identity_parts=(f"page={page}",),
            payload=payload,
        )

    async def publish_resume(
        self,
        *,
        context: HHRawContext,
        resume_id: str,
        payload: Any,
    ) -> HHRawObject:
        """Публикует один точный full resume response"""

        return await self._publish(
            context=context,
            kind=HHRawObjectKind.RESUME,
            identity_parts=(f"resume_id={_segment(resume_id)}",),
            payload=payload,
        )
