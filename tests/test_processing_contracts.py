from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from careerops_processing.contracts import (
    BindingSnapshot,
    DataQualityStatus,
    EntityType,
    NormalizedRef,
    ProcessingInputManifest,
    ProcessingVersionBundle,
    RawObservationRef,
    SourceValue,
    TargetPolicy,
    validate_bundle_for_ref,
    ValueState,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def raw_ref(*, sha: str, hour: int = 10) -> RawObservationRef:
    return RawObservationRef(
        raw_uri=f"s3://careerops-raw/test/{sha}.json",
        raw_sha256=sha,
        observed_at=datetime(2026, 9, 7, hour, tzinfo=UTC),
    )


def normalized_ref(*, entity_type: EntityType, sha: str) -> NormalizedRef:
    is_resume = entity_type is EntityType.RESUME
    return NormalizedRef(
        entity_type=entity_type,
        source_key="hh",
        source_entity_id="resume-1" if is_resume else "vacancy-1",
        account_key="junior" if is_resume else None,
        raw=raw_ref(sha=HASH_A if is_resume else HASH_B),
        normalized_uri=f"s3://careerops-lake/normalized/{entity_type.value}/{sha}.json",
        normalized_sha256=sha,
        semantic_content_hash=HASH_C if is_resume else HASH_D,
        schema_version=f"careerops.hh.{entity_type.value}.normalized.v1",
        normalization_version="spark-hh-normalizer-1",
        dictionary_version="careerops-dictionary-2026-09",
        materialization_key=f"{entity_type.value}:1",
        processing_ready=True,
        dq_status=DataQualityStatus.CLEAN,
    )


def manifest() -> ProcessingInputManifest:
    policy = TargetPolicy.from_content(
        target_key="python_backend_junior",
        schema_version="careerops.target-policy.v1",
        policy_version="python-backend-junior-1",
        content={
            "role_families": ["python_backend"],
            "hard_constraints": {"management": "forbidden"},
        },
    )
    return ProcessingInputManifest(
        vacancy=normalized_ref(entity_type=EntityType.VACANCY, sha=HASH_A),
        resume=normalized_ref(entity_type=EntityType.RESUME, sha=HASH_B),
        binding=BindingSnapshot(
            binding_key="backend_junior",
            binding_version=3,
            account_key="junior",
            source_resume_id="resume-1",
            target_key="python_backend_junior",
        ),
        target_policy=policy,
        versions=ProcessingVersionBundle(
            pipeline_version="processing-v2",
            dictionary_version="careerops-dictionary-2026-09",
            requirement_extraction_version="requirements-v1",
            evidence_version="evidence-v1",
            qualification_version="qualification-v1",
            scoring_version="scoring-v1",
            calibration_version="calibration-unset",
        ),
        as_of=datetime(2026, 9, 7, 12, tzinfo=UTC),
    )


def test_contracts_do_not_coerce_cross_component_types() -> None:
    with pytest.raises(ValidationError):
        BindingSnapshot(
            binding_key="backend_junior",
            binding_version="3",
            account_key="junior",
            source_resume_id="resume-1",
            target_key="python_backend_junior",
        )


def test_source_value_preserves_unknown_semantics() -> None:
    assert SourceValue[bool](state=ValueState.NOT_PROVIDED).value is None
    assert SourceValue[bool](state=ValueState.KNOWN, value=False).value is False

    with pytest.raises(ValidationError):
        SourceValue[bool](state=ValueState.KNOWN)

    with pytest.raises(ValidationError):
        SourceValue[bool](state=ValueState.UNKNOWN_PARSE, value=False)


def test_normalized_ref_requires_account_scope_for_resume() -> None:
    with pytest.raises(ValidationError):
        NormalizedRef(
            entity_type=EntityType.RESUME,
            source_key="hh",
            source_entity_id="resume-1",
            raw=raw_ref(sha=HASH_A),
            normalized_uri="s3://careerops-lake/resume.json",
            normalized_sha256=HASH_B,
            semantic_content_hash=HASH_C,
            schema_version="resume-v1",
            normalization_version="normalizer-v1",
            dictionary_version="dict-v1",
            materialization_key="resume:1",
            processing_ready=True,
            dq_status=DataQualityStatus.CLEAN,
        )


def test_target_policy_hashes_canonical_content() -> None:
    left = TargetPolicy.from_content(
        target_key="de",
        schema_version="policy-v1",
        policy_version="de-v1",
        content={"b": [2, 1], "a": {"z": True}},
    )
    right = TargetPolicy.from_content(
        target_key="de",
        schema_version="policy-v1",
        policy_version="de-v1",
        content={"a": {"z": True}, "b": [2, 1]},
    )
    assert left.content_sha256 == right.content_sha256
    assert left.content_json == right.content_json

    with pytest.raises(ValidationError):
        TargetPolicy(
            target_key=left.target_key,
            schema_version=left.schema_version,
            policy_version=left.policy_version,
            content_sha256=HASH_A,
            content_json=left.content_json,
        )


def test_manifest_fingerprint_is_stable_and_semantic() -> None:
    first = manifest()
    second = ProcessingInputManifest.model_validate_json(first.model_dump_json())

    assert first.canonical_json() == second.canonical_json()
    assert first.input_fingerprint() == second.input_fingerprint()
    assert len(first.input_fingerprint()) == 64

    changed = first.model_copy(
        update={
            "versions": first.versions.model_copy(update={"scoring_version": "scoring-v2"})
        }
    )
    assert changed.input_fingerprint() != first.input_fingerprint()


def test_manifest_rejects_binding_resume_identity_mismatch() -> None:
    value = manifest()
    with pytest.raises(ValidationError):
        ProcessingInputManifest.model_validate(
            {
                **value.model_dump(mode="python"),
                "binding": value.binding.model_copy(update={"source_resume_id": "other-resume"}),
            }
        )


def test_manifest_rejects_unready_inputs() -> None:
    value = manifest()
    with pytest.raises(ValidationError):
        ProcessingInputManifest.model_validate(
            {
                **value.model_dump(mode="python"),
                "vacancy": value.vacancy.model_copy(update={"processing_ready": False}),
            }
        )


def test_loaded_bundle_must_match_published_ref() -> None:
    from careerops_processing.contracts import (
        DataQualityReport,
        Employer,
        NormalizedVacancy,
    )

    ref = normalized_ref(entity_type=EntityType.VACANCY, sha=HASH_A)
    bundle = NormalizedVacancy(
        schema_version=ref.schema_version,
        normalization_version=ref.normalization_version,
        dictionary_version=ref.dictionary_version,
        materialization_key=ref.materialization_key,
        source_key=ref.source_key,
        source_entity_id=ref.source_entity_id,
        raw=ref.raw,
        semantic_content_hash=ref.semantic_content_hash,
        title=SourceValue(state=ValueState.KNOWN, value="Data Engineer"),
        employer=SourceValue(state=ValueState.KNOWN, value=Employer(name="Example")),
        experience=SourceValue(state=ValueState.NOT_PROVIDED),
        location=SourceValue(state=ValueState.NOT_PROVIDED),
        salary=SourceValue(state=ValueState.NOT_PROVIDED),
        archived=SourceValue(state=ValueState.KNOWN, value=False),
        closed_for_applicants=SourceValue(state=ValueState.KNOWN, value=False),
        published_at=SourceValue(state=ValueState.NOT_PROVIDED),
        dq=DataQualityReport(
            status=DataQualityStatus.CLEAN,
            full_entity_available=True,
            parse_complete=True,
        ),
    )

    validate_bundle_for_ref(ref, bundle, actual_normalized_sha256=HASH_A)

    with pytest.raises(ValueError, match="SHA-256"):
        validate_bundle_for_ref(ref, bundle, actual_normalized_sha256=HASH_B)
