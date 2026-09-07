"""Versioned binding and target-policy snapshots pinned into processing jobs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import model_validator

from .common import FrozenModel, NonEmptyStr, Sha256, VersionId

JsonObject = dict[str, Any]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class TargetPolicy(FrozenModel):
    """Immutable target policy snapshot; semantic schema is versioned independently."""

    target_key: NonEmptyStr
    schema_version: VersionId
    policy_version: VersionId
    content_sha256: Sha256
    content_json: NonEmptyStr

    @model_validator(mode="after")
    def validate_content_hash(self) -> TargetPolicy:
        try:
            content = json.loads(self.content_json)
        except json.JSONDecodeError as exc:
            raise ValueError("content_json must contain valid JSON") from exc
        if not isinstance(content, dict):
            raise ValueError("target policy content must be a JSON object")
        canonical = _canonical_json(content)
        if canonical != self.content_json:
            raise ValueError("content_json must use canonical JSON serialization")
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if digest != self.content_sha256:
            raise ValueError("content_sha256 does not match canonical target policy content")
        return self

    @classmethod
    def from_content(
        cls,
        *,
        target_key: str,
        schema_version: str,
        policy_version: str,
        content: JsonObject,
    ) -> TargetPolicy:
        canonical = _canonical_json(content)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(
            target_key=target_key,
            schema_version=schema_version,
            policy_version=policy_version,
            content_sha256=digest,
            content_json=canonical,
        )

    def parsed_content(self) -> JsonObject:
        value = json.loads(self.content_json)
        if not isinstance(value, dict):
            raise ValueError("target policy content must be a JSON object")
        return value


class BindingSnapshot(FrozenModel):
    """Semantic pair identity pinned for one vacancy × resume evaluation."""

    binding_key: NonEmptyStr
    binding_version: int
    account_key: NonEmptyStr
    source_resume_id: NonEmptyStr
    target_key: NonEmptyStr

    @model_validator(mode="after")
    def validate_binding_version(self) -> BindingSnapshot:
        if self.binding_version < 1:
            raise ValueError("binding_version must be >= 1")
        return self
