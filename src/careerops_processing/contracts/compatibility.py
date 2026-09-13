"""Проверка соответствия внешнего NormalizedRef загруженному bundle"""

from __future__ import annotations

from .common import EntityType, NormalizedRef, Sha256
from .normalized import NormalizedResume, NormalizedVacancy


def validate_bundle_for_ref(
    ref: NormalizedRef,
    bundle: NormalizedVacancy | NormalizedResume,
    *,
    actual_normalized_sha256: Sha256,
) -> None:
    """Отклоняет bundle, который не совпадает с опубликованным current ref"""

    expected_type = (
        EntityType.VACANCY if isinstance(bundle, NormalizedVacancy) else EntityType.RESUME
    )
    if ref.entity_type is not expected_type:
        raise ValueError("normalized ref entity type does not match loaded bundle")
    if actual_normalized_sha256 != ref.normalized_sha256:
        raise ValueError("loaded bundle SHA-256 does not match normalized ref")
    if bundle.source_key != ref.source_key or bundle.source_entity_id != ref.source_entity_id:
        raise ValueError("loaded bundle source identity does not match normalized ref")
    if isinstance(bundle, NormalizedResume) and bundle.account_key != ref.account_key:
        raise ValueError("loaded resume account scope does not match normalized ref")
    if bundle.raw != ref.raw:
        raise ValueError("loaded bundle RAW provenance does not match normalized ref")
    if bundle.semantic_content_hash != ref.semantic_content_hash:
        raise ValueError("loaded bundle semantic content hash does not match normalized ref")
    if bundle.schema_version != ref.schema_version:
        raise ValueError("loaded bundle schema version does not match normalized ref")
    if bundle.normalization_version != ref.normalization_version:
        raise ValueError("loaded bundle normalization version does not match normalized ref")
    if bundle.dictionary_version != ref.dictionary_version:
        raise ValueError("loaded bundle dictionary version does not match normalized ref")
    if bundle.materialization_key != ref.materialization_key:
        raise ValueError("loaded bundle materialization key does not match normalized ref")
    if bundle.dq.status is not ref.dq_status:
        raise ValueError("loaded bundle DQ status does not match normalized ref")
    if bundle.dq.processing_ready != ref.processing_ready:
        raise ValueError("loaded bundle processing readiness does not match normalized ref")
