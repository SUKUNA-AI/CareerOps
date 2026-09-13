"""JSON and Markdown rendering for calibration-v1 reports."""

from __future__ import annotations

import json
from pathlib import Path


def write_json_report(report: dict[str, object], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _metric(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_markdown_report(report: dict[str, object]) -> str:
    lines = ["# CareerOPS calibration report", ""]
    dataset = report.get("dataset")
    if isinstance(dataset, dict):
        lines.extend(
            [
                "## Dataset",
                "",
                f"- dataset: `{dataset.get('dataset_id', 'unknown')}`",
                f"- label tier: `{dataset.get('label_tier', 'unknown')}`",
                f"- evaluated split: `{report.get('split', 'unknown')}`",
                f"- annotator model: `{dataset.get('annotator_model') or 'MISSING'}`",
                f"- provenance complete: `{dataset.get('provenance_complete', False)}`",
                "",
            ]
        )

    stages = report.get("stages")
    if not isinstance(stages, dict):
        return "\n".join(lines) + "\n"

    preferred_metrics = {
        "P2-03": (
            "evaluated_pairs",
            "retention_false_negative_rate",
            "retention_recall",
            "exclusion_precision",
            "exclusion_recall",
        ),
        "P2-04": (
            "requirement_recall",
            "requirement_precision",
            "significant_requirement_recall",
            "evidence_recall",
            "evidence_precision",
        ),
        "P2-05": (
            "queries_with_gold_evidence",
            "recall_at_1",
            "recall_at_3",
            "recall_at_5",
            "mrr",
            "ndcg_at_5",
            "map_at_5",
        ),
        "P2-06": (
            "coverage",
            "accuracy_on_evaluated",
            "gold_state_counts",
            "state_recall",
        ),
        "P2-07": (
            "evaluated_pairs",
            "application_candidate_recall",
            "application_candidate_precision",
            "skip_precision",
            "skip_recall",
            "review_rate",
            "accuracy",
        ),
    }
    for stage_name in ("P2-03", "P2-04", "P2-05", "P2-06", "P2-07"):
        stage = stages.get(stage_name)
        lines.extend([f"## {stage_name}", ""])
        if not isinstance(stage, dict):
            lines.extend(["Not evaluated.", ""])
            continue
        for key in preferred_metrics[stage_name]:
            if key in stage:
                lines.append(f"- `{key}`: {_metric(stage[key])}")
        lines.append("")

    return "\n".join(lines) + "\n"


def write_markdown_report(report: dict[str, object], path: str | Path) -> None:
    Path(path).write_text(render_markdown_report(report), encoding="utf-8")
