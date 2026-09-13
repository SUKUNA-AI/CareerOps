"""Dataset import, validation, provenance and leakage-safe vacancy-grouped splitting."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel

from .models import (
    AstraAnnotation,
    AstraDecision,
    CalibrationBatchProjection,
    CalibrationManifest,
    PairMetadata,
    SplitAssignment,
    SplitName,
)


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl[ModelT: BaseModel](
    path: str | Path,
    model: type[ModelT],
) -> tuple[ModelT, ...]:
    values: list[ModelT] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                values.append(model.model_validate_json(line))
            except ValueError as exc:
                raise ValueError(f"invalid JSONL row {line_number} in {path}: {exc}") from exc
    if not values:
        raise ValueError(f"JSONL file contains no records: {path}")
    return tuple(values)


def load_annotations(path: str | Path) -> tuple[AstraAnnotation, ...]:
    annotations = load_jsonl(path, AstraAnnotation)
    pair_ids = [item.pair_id for item in annotations]
    if len(pair_ids) != len(set(pair_ids)):
        raise ValueError("annotation pair_id values must be unique")
    return annotations


def load_pair_metadata(path: str | Path) -> tuple[PairMetadata, ...]:
    batches = load_jsonl(path, CalibrationBatchProjection)
    values: list[PairMetadata] = []
    seen: set[str] = set()
    for batch in batches:
        for pair in batch.pairs:
            if pair.pair_id in seen:
                raise ValueError(f"duplicate pair_id in Astra batches: {pair.pair_id}")
            seen.add(pair.pair_id)
            values.append(
                PairMetadata(
                    pair_id=pair.pair_id,
                    source_vacancy_id=pair.vacancy.source_vacancy_id,
                    resume_key=batch.resume.calibration_resume_key,
                    source_resume_id=batch.resume.source_resume_id,
                )
            )
    if not values:
        raise ValueError("Astra batches contain no pairs")
    return tuple(values)


def validate_pair_coverage(
    annotations: tuple[AstraAnnotation, ...],
    metadata: tuple[PairMetadata, ...],
) -> None:
    annotation_ids = {item.pair_id for item in annotations}
    metadata_ids = {item.pair_id for item in metadata}
    missing_annotations = sorted(metadata_ids - annotation_ids)
    unknown_annotations = sorted(annotation_ids - metadata_ids)
    if missing_annotations or unknown_annotations:
        raise ValueError(
            "annotation/batch pair coverage mismatch: "
            f"missing_annotations={len(missing_annotations)} "
            f"unknown_annotations={len(unknown_annotations)}"
        )


def _ordered_vacancy_ids(vacancy_ids: set[str], seed: str) -> list[str]:
    def order_key(vacancy_id: str) -> str:
        payload = f"{seed}\x00{vacancy_id}".encode()
        return hashlib.sha256(payload).hexdigest()

    return sorted(vacancy_ids, key=order_key)


def build_grouped_split(
    metadata: tuple[PairMetadata, ...],
    *,
    seed: str,
    calibration_fraction: Decimal = Decimal("0.70"),
    validation_fraction: Decimal = Decimal("0.15"),
    holdout_fraction: Decimal = Decimal("0.15"),
) -> tuple[SplitAssignment, ...]:
    if calibration_fraction + validation_fraction + holdout_fraction != Decimal("1"):
        raise ValueError("split fractions must sum to exactly 1")
    if min(calibration_fraction, validation_fraction, holdout_fraction) <= 0:
        raise ValueError("split fractions must be positive")

    vacancy_ids = {item.source_vacancy_id for item in metadata}
    ordered = _ordered_vacancy_ids(vacancy_ids, seed)
    total = len(ordered)
    calibration_count = int(Decimal(total) * calibration_fraction)
    validation_count = int(Decimal(total) * validation_fraction)
    if calibration_count == 0 or validation_count == 0:
        raise ValueError("dataset is too small for requested grouped split")

    calibration_ids = set(ordered[:calibration_count])
    validation_ids = set(ordered[calibration_count : calibration_count + validation_count])
    holdout_ids = set(ordered[calibration_count + validation_count :])
    if not holdout_ids:
        raise ValueError("dataset is too small to create a non-empty holdout split")
    leaked = (
        calibration_ids & validation_ids
        or calibration_ids & holdout_ids
        or validation_ids & holdout_ids
    )
    if leaked:
        raise AssertionError("vacancy groups leaked across splits")

    values: list[SplitAssignment] = []
    for item in metadata:
        if item.source_vacancy_id in calibration_ids:
            split = SplitName.CALIBRATION
        elif item.source_vacancy_id in validation_ids:
            split = SplitName.VALIDATION
        else:
            split = SplitName.HOLDOUT
        values.append(
            SplitAssignment(
                pair_id=item.pair_id,
                source_vacancy_id=item.source_vacancy_id,
                split=split,
            )
        )
    return tuple(sorted(values, key=lambda item: item.pair_id))


def build_manifest(
    *,
    dataset_id: str,
    annotations_path: str | Path,
    batches_path: str | Path,
    annotations: tuple[AstraAnnotation, ...],
    metadata: tuple[PairMetadata, ...],
    split_seed: str,
    calibration_fraction: Decimal = Decimal("0.70"),
    validation_fraction: Decimal = Decimal("0.15"),
    holdout_fraction: Decimal = Decimal("0.15"),
    annotator_model_override: str | None = None,
) -> CalibrationManifest:
    validate_pair_coverage(annotations, metadata)
    policy_versions = {item.annotation_policy_version for item in annotations}
    sources = {item.annotation_source for item in annotations}
    if len(policy_versions) != 1:
        raise ValueError("calibration dataset must contain one annotation_policy_version")
    if sources != {"astra"}:
        raise ValueError(f"unsupported annotation sources: {sorted(sources)}")

    source_models = {item.annotator_model for item in annotations if item.annotator_model}
    if annotator_model_override is not None:
        annotator_model = annotator_model_override
    elif len(source_models) == 1:
        annotator_model = next(iter(source_models))
    elif not source_models:
        annotator_model = None
    else:
        raise ValueError("dataset contains multiple annotator_model values; provide override")

    return CalibrationManifest(
        dataset_id=dataset_id,
        annotation_policy_version=next(iter(policy_versions)),
        annotator_model=annotator_model,
        provenance_complete=annotator_model is not None,
        annotations_sha256=_sha256_file(annotations_path),
        batches_sha256=_sha256_file(batches_path),
        split_seed=split_seed,
        calibration_fraction=calibration_fraction,
        validation_fraction=validation_fraction,
        holdout_fraction=holdout_fraction,
        pair_count=len(annotations),
        unique_vacancies=len({item.source_vacancy_id for item in metadata}),
        unique_resumes=len({item.resume_key for item in metadata}),
    )


def build_label_summary(
    annotations: tuple[AstraAnnotation, ...],
    assignments: tuple[SplitAssignment, ...],
) -> dict[str, object]:
    assignment_by_pair = {item.pair_id: item.split for item in assignments}
    if set(assignment_by_pair) != {item.pair_id for item in annotations}:
        raise ValueError("split assignments do not cover annotations exactly")

    decision_counts = Counter(item.decision.value for item in annotations)
    split_decisions: dict[str, Counter[str]] = defaultdict(Counter)
    confidence_counts: Counter[str] = Counter()
    sufficiency_counts: Counter[str] = Counter()
    requirement_status_counts: Counter[str] = Counter()
    requirement_importance_counts: Counter[str] = Counter()
    requirements_with_evidence = 0
    requirement_count = 0

    for annotation in annotations:
        split_name = assignment_by_pair[annotation.pair_id].value
        split_decisions[split_name][annotation.decision.value] += 1
        confidence_counts[annotation.confidence.value] += 1
        sufficiency_counts[annotation.data_sufficiency.value] += 1
        for requirement in annotation.requirements:
            requirement_count += 1
            requirement_status_counts[requirement.status.value] += 1
            requirement_importance_counts[requirement.importance.value] += 1
            if requirement.evidence:
                requirements_with_evidence += 1

    return {
        "pair_count": len(annotations),
        "decision_counts": dict(sorted(decision_counts.items())),
        "split_decision_counts": {
            split: dict(sorted(counts.items()))
            for split, counts in sorted(split_decisions.items())
        },
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "data_sufficiency_counts": dict(sorted(sufficiency_counts.items())),
        "requirement_count": requirement_count,
        "requirements_with_evidence": requirements_with_evidence,
        "requirement_status_counts": dict(sorted(requirement_status_counts.items())),
        "requirement_importance_counts": dict(sorted(requirement_importance_counts.items())),
        "application_candidate_count": decision_counts[AstraDecision.APPLICATION_CANDIDATE.value],
    }


def write_prepared_dataset(
    *,
    output_dir: str | Path,
    manifest: CalibrationManifest,
    assignments: tuple[SplitAssignment, ...],
    summary: dict[str, object],
) -> None:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (root / "split_assignments.jsonl").open("w", encoding="utf-8") as handle:
        for item in assignments:
            handle.write(json.dumps(item.model_dump(mode="json"), sort_keys=True) + "\n")
    (root / "label_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
