"""CLI for preparing, evaluating and searching CareerOPS calibration-v1 datasets."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from .dataset import (
    build_grouped_split,
    build_label_summary,
    build_manifest,
    load_annotations,
    load_jsonl,
    load_pair_metadata,
    write_prepared_dataset,
)
from .evaluators import (
    evaluate_p203,
    evaluate_p204,
    evaluate_p205,
    evaluate_p206,
    evaluate_p207,
)
from .models import (
    CalibrationManifest,
    P203Prediction,
    P204Prediction,
    P205Prediction,
    P206Prediction,
    P207PolicyCandidate,
    P207Prediction,
    P207ReplayCase,
    SplitAssignment,
    SplitName,
)
from .policy_search import search_p207_policy
from .reporting import write_json_report, write_markdown_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="careerops-calibration")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="validate Astra labels and build grouped split")
    prepare.add_argument("--annotations", required=True)
    prepare.add_argument("--batches", required=True)
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--dataset-id", default="astra-calibration-v1")
    prepare.add_argument("--split-seed", default="careerops-calibration-v1")
    prepare.add_argument("--annotator-model")

    evaluate = subparsers.add_parser("evaluate", help="evaluate available P2 stage predictions")
    evaluate.add_argument("--annotations", required=True)
    evaluate.add_argument("--manifest", required=True)
    evaluate.add_argument("--assignments", required=True)
    evaluate.add_argument("--predictions-dir", required=True)
    evaluate.add_argument("--split", choices=[item.value for item in SplitName], required=True)
    evaluate.add_argument("--output-dir", required=True)

    search = subparsers.add_parser("search-p207", help="rank P2-07 policy candidates")
    search.add_argument("--annotations", required=True)
    search.add_argument("--assignments", required=True)
    search.add_argument("--replay", required=True)
    search.add_argument("--candidates", required=True)
    search.add_argument("--split", choices=[item.value for item in SplitName], required=True)
    search.add_argument("--output", required=True)
    search.add_argument("--top", type=int, default=100)
    return parser


def _selected_pair_ids(
    assignments: tuple[SplitAssignment, ...],
    split: SplitName,
) -> set[str]:
    return {item.pair_id for item in assignments if item.split is split}


def _prepare(args: argparse.Namespace) -> int:
    annotations = load_annotations(args.annotations)
    metadata = load_pair_metadata(args.batches)
    assignments = build_grouped_split(
        metadata,
        seed=args.split_seed,
        calibration_fraction=Decimal("0.70"),
        validation_fraction=Decimal("0.15"),
        holdout_fraction=Decimal("0.15"),
    )
    manifest = build_manifest(
        dataset_id=args.dataset_id,
        annotations_path=args.annotations,
        batches_path=args.batches,
        annotations=annotations,
        metadata=metadata,
        split_seed=args.split_seed,
        annotator_model_override=args.annotator_model,
    )
    summary = build_label_summary(annotations, assignments)
    write_prepared_dataset(
        output_dir=args.output_dir,
        manifest=manifest,
        assignments=assignments,
        summary=summary,
    )
    print(
        json.dumps(
            {
                "dataset_id": manifest.dataset_id,
                "pair_count": manifest.pair_count,
                "unique_vacancies": manifest.unique_vacancies,
                "unique_resumes": manifest.unique_resumes,
                "provenance_complete": manifest.provenance_complete,
                "output_dir": str(Path(args.output_dir).resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    annotations = load_annotations(args.annotations)
    assignments = load_jsonl(args.assignments, SplitAssignment)
    split = SplitName(args.split)
    allowed = _selected_pair_ids(assignments, split)
    selected = tuple(item for item in annotations if item.pair_id in allowed)
    if not selected:
        raise ValueError(f"split contains no annotations: {split.value}")

    predictions_dir = Path(args.predictions_dir)
    stages: dict[str, object] = {}

    p203_path = predictions_dir / "p203.jsonl"
    if p203_path.exists():
        stages["P2-03"] = evaluate_p203(selected, load_jsonl(p203_path, P203Prediction))

    p204_path = predictions_dir / "p204.jsonl"
    if p204_path.exists():
        stages["P2-04"] = evaluate_p204(selected, load_jsonl(p204_path, P204Prediction))

    p205_path = predictions_dir / "p205.jsonl"
    if p205_path.exists():
        stages["P2-05"] = evaluate_p205(selected, load_jsonl(p205_path, P205Prediction))

    p206_path = predictions_dir / "p206.jsonl"
    if p206_path.exists():
        stages["P2-06"] = evaluate_p206(selected, load_jsonl(p206_path, P206Prediction))

    p207_path = predictions_dir / "p207.jsonl"
    if p207_path.exists():
        stages["P2-07"] = evaluate_p207(selected, load_jsonl(p207_path, P207Prediction))

    manifest = CalibrationManifest.model_validate_json(Path(args.manifest).read_text(encoding="utf-8"))
    report: dict[str, object] = {
        "schema_version": "careerops.calibration-report.v1",
        "dataset": manifest.model_dump(mode="json"),
        "split": split.value,
        "evaluated_pair_count": len(selected),
        "stages": stages,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json_report(report, output_dir / "calibration_report.json")
    write_markdown_report(report, output_dir / "calibration_report.md")
    print(json.dumps({"split": split.value, "stages": sorted(stages)}, sort_keys=True))
    return 0


def _search_p207(args: argparse.Namespace) -> int:
    if args.top <= 0:
        raise ValueError("--top must be positive")
    annotations = load_annotations(args.annotations)
    assignments = load_jsonl(args.assignments, SplitAssignment)
    replay_cases = load_jsonl(args.replay, P207ReplayCase)
    candidates = load_jsonl(args.candidates, P207PolicyCandidate)
    split = SplitName(args.split)
    allowed = _selected_pair_ids(assignments, split)
    results = search_p207_policy(
        annotations,
        replay_cases,
        candidates,
        allowed_pair_ids=allowed,
    )
    payload = {
        "schema_version": "careerops.p207-policy-search.v1",
        "split": split.value,
        "candidate_count": len(results),
        "results": results[: args.top],
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"split": split.value, "candidate_count": len(results)}, sort_keys=True))
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "prepare":
        return _prepare(args)
    if args.command == "evaluate":
        return _evaluate(args)
    if args.command == "search-p207":
        return _search_p207(args)
    raise AssertionError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
