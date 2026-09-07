"""Canonical processing input manifest and deterministic fingerprint."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from pydantic import model_validator

from .common import EntityType, FrozenModel, NormalizedRef, Sha256, VersionId
from .policy import BindingSnapshot, TargetPolicy
from .versions import ProcessingVersionBundle

MANIFEST_SCHEMA_VERSION = "careerops.processing.input-manifest.v1"


class ProcessingInputManifest(FrozenModel):
    """Exact immutable semantic input for one vacancy × resume processing job."""

    manifest_schema_version: VersionId = MANIFEST_SCHEMA_VERSION
    vacancy: NormalizedRef
    resume: NormalizedRef
    binding: BindingSnapshot
    target_policy: TargetPolicy
    versions: ProcessingVersionBundle
    as_of: datetime

    @model_validator(mode="after")
    def validate_manifest(self) -> ProcessingInputManifest:
        if self.vacancy.entity_type is not EntityType.VACANCY:
            raise ValueError("vacancy ref must have entity_type=vacancy")
        if self.resume.entity_type is not EntityType.RESUME:
            raise ValueError("resume ref must have entity_type=resume")
        if not self.vacancy.processing_ready or not self.resume.processing_ready:
            raise ValueError("normalized refs must be processing_ready")
        if self.resume.account_key != self.binding.account_key:
            raise ValueError("resume account_key does not match binding snapshot")
        if self.resume.source_entity_id != self.binding.source_resume_id:
            raise ValueError("resume source identity does not match binding snapshot")
        if self.binding.target_key != self.target_policy.target_key:
            raise ValueError("binding target_key does not match target policy")
        if self.vacancy.dictionary_version != self.versions.dictionary_version:
            raise ValueError("vacancy dictionary version does not match processing bundle")
        if self.resume.dictionary_version != self.versions.dictionary_version:
            raise ValueError("resume dictionary version does not match processing bundle")

        value = self.as_of
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        normalized = value.astimezone(UTC)
        if normalized < self.vacancy.raw.observed_at or normalized < self.resume.raw.observed_at:
            raise ValueError("as_of cannot predate either normalized source observation")
        if normalized != value:
            object.__setattr__(self, "as_of", normalized)
        return self

    def canonical_json(self) -> str:
        """Return the exact canonical JSON body used for input fingerprinting."""

        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def input_fingerprint(self) -> Sha256:
        """Hash semantic inputs only; no job id, claim time or artifact URI is included."""

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
