from __future__ import annotations

from datetime import UTC, datetime

from careerops_processing.contracts import (
    BindingSnapshot,
    DataQualityStatus,
    EntityType,
    JinaVersionBundle,
    NormalizedRef,
    ProcessingInputManifest,
    ProcessingVersionBundle,
    RawObservationRef,
    TargetPolicy,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _ref(entity_type: EntityType) -> NormalizedRef:
    is_resume = entity_type is EntityType.RESUME
    return NormalizedRef(
        entity_type=entity_type,
        source_key="hh",
        source_entity_id="resume-1" if is_resume else "vacancy-1",
        account_key="account-1" if is_resume else None,
        raw=RawObservationRef(
            raw_uri=f"s3://careerops-raw/{entity_type.value}.json",
            raw_sha256=HASH_B if is_resume else HASH_A,
            observed_at=datetime(2026, 9, 9, 10, tzinfo=UTC),
        ),
        normalized_uri=f"s3://careerops-lake/{entity_type.value}.json",
        normalized_sha256=HASH_D if is_resume else HASH_C,
        semantic_content_hash=HASH_B if is_resume else HASH_A,
        schema_version=f"{entity_type.value}-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        materialization_key=f"{entity_type.value}:1",
        processing_ready=True,
        dq_status=DataQualityStatus.CLEAN,
    )


def _jina() -> JinaVersionBundle:
    return JinaVersionBundle(
        model_id="jinaai/jina-reranker-v3.5",
        model_revision="model-rev",
        model_code_revision="code-rev",
        tokenizer_revision="tokenizer-rev",
        runtime_backend="transformers-cuda",
        dtype_or_quantization="float16",
        torch_version="2.8.0+cu128",
        transformers_version="4.57.3",
        rendering_version="p205-render-v1",
        selection_version="p205-selection-v1",
        block_protocol="single-list-v1",
        token_budget=4096,
        top_k=3,
    )


def _manifest(*, jina: JinaVersionBundle | None, pipeline_version: str) -> ProcessingInputManifest:
    policy = TargetPolicy.from_content(
        target_key="de",
        schema_version="target-policy-v1",
        policy_version="policy-v1",
        content={"filtering": {"schema_version": 1}},
    )
    return ProcessingInputManifest(
        vacancy=_ref(EntityType.VACANCY),
        resume=_ref(EntityType.RESUME),
        binding=BindingSnapshot(
            binding_key="binding-1",
            binding_version=1,
            account_key="account-1",
            source_resume_id="resume-1",
            target_key="de",
        ),
        target_policy=policy,
        versions=ProcessingVersionBundle(
            pipeline_version=pipeline_version,
            dictionary_version="dict-v1",
            filter_version="filter-v1",
            requirement_extraction_version="requirements-v2",
            evidence_version="evidence-v2",
            qualification_version="qualification-v1",
            scoring_version="scoring-v1",
            calibration_version="calibration-unset",
            jina=jina,
        ),
        as_of=datetime(2026, 9, 9, 12, tzinfo=UTC),
    )


def test_historical_p204_manifest_without_jina_remains_valid() -> None:
    manifest = _manifest(jina=None, pipeline_version="processing-v2-p204")

    assert manifest.versions.jina is None
    assert ProcessingInputManifest.model_validate_json(manifest.model_dump_json()) == manifest


def test_p205_manifest_pins_complete_jina_runtime_identity() -> None:
    manifest = _manifest(jina=_jina(), pipeline_version="processing-v2-p205")

    assert manifest.versions.jina is not None
    assert manifest.versions.jina.torch_version == "2.8.0+cu128"
    assert manifest.versions.jina.transformers_version == "4.57.3"


def test_p205_cutover_changes_manifest_fingerprint() -> None:
    p204 = _manifest(jina=None, pipeline_version="processing-v2-p204")
    p205 = _manifest(jina=_jina(), pipeline_version="processing-v2-p205")

    assert p204.input_fingerprint() != p205.input_fingerprint()