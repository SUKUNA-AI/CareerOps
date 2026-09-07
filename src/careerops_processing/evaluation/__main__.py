"""CLI для воспроизводимого запуска Processing evaluation corpus"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from careerops_processing.infrastructure.policy_repository import FileTargetPolicyRepository

from .filter_bootstrap import build_filter_bootstrap_corpus
from .filter_gold import evaluate_filter_gold, write_filter_gold_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="careerops-processing-evaluation")
    parser.add_argument(
        "--policy-dir",
        default="config/processing/target_policies",
    )
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository = FileTargetPolicyRepository(Path(args.policy_dir))
    corpus = build_filter_bootstrap_corpus(repository)
    report = evaluate_filter_gold(corpus)

    if args.output:
        write_filter_gold_report(report, args.output)

    print(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    if report.false_exclusions or report.missed_exclusions or report.reason_mismatches:
        return 2
    return 0


raise SystemExit(main())
