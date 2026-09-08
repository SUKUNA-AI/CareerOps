"""S3 loader pinned manifest и normalized inputs для Processing executor"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from careerops_processing.contracts import (
    EntityType,
    NormalizedRef,
    NormalizedResume,
    NormalizedVacancy,
    ProcessingInputManifest,
    validate_bundle_for_ref,
)
from careerops_storage.s3 import S3JsonStore

_SHA_FILE = re.compile(r"^(?P<sha>[0-9a-f]{64})\.json$")


class S3ProcessingInputLoader:
    """Читает pinned inputs и проверяет identity до передачи в business executor"""

    def __init__(
        self,
        *,
        manifest_store: S3JsonStore,
        normalized_store: S3JsonStore,
    ) -> None:
        self._manifest_store = manifest_store
        self._normalized_store = normalized_store

    @staticmethod
    def _manifest_sha_from_uri(uri: str) -> str:
        parsed = urlsplit(uri)
        filename = parsed.path.rsplit("/", 1)[-1]
        match = _SHA_FILE.fullmatch(filename)
        if match is None:
            raise ValueError("input manifest URI не является content-addressed JSON")
        return match.group("sha")

    async def load_manifest(self, uri: str) -> ProcessingInputManifest:
        payload, object_ref = await self._manifest_store.get_json_with_metadata(uri)
        expected_sha = self._manifest_sha_from_uri(uri)
        if object_ref.sha256 != expected_sha:
            raise ValueError("input manifest content hash не совпадает с URI")
        return ProcessingInputManifest.model_validate(payload)

    async def load_vacancy(self, ref: NormalizedRef) -> NormalizedVacancy:
        if ref.entity_type is not EntityType.VACANCY:
            raise ValueError("vacancy loader требует vacancy NormalizedRef")
        payload, object_ref = await self._normalized_store.get_json_with_metadata(
            ref.normalized_uri
        )
        bundle = NormalizedVacancy.model_validate(payload)
        validate_bundle_for_ref(
            ref,
            bundle,
            actual_normalized_sha256=object_ref.sha256,
        )
        return bundle

    async def load_resume(self, ref: NormalizedRef) -> NormalizedResume:
        if ref.entity_type is not EntityType.RESUME:
            raise ValueError("resume loader требует resume NormalizedRef")
        payload, object_ref = await self._normalized_store.get_json_with_metadata(
            ref.normalized_uri
        )
        bundle = NormalizedResume.model_validate(payload)
        validate_bundle_for_ref(
            ref,
            bundle,
            actual_normalized_sha256=object_ref.sha256,
        )
        return bundle
