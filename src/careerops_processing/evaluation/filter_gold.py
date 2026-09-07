"""Gold evaluation для high-recall фильтра Processing v2"""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from pydantic import Field

from careerops_processing.contracts import (
    FilterOutcome,
    NormalizedResume,
    NormalizedVacancy,
    TargetPolicy,
)
from careerops_processing.contracts.common import FrozenModel
from careerops_processing.core import evaluate_filter


class FilterGoldCorpusKind(StrEnum):
    BOOTSTRAP_ADVERSARIAL = "bootstrap_adversarial"
    REAL_ANNOTATED = "real_annotated"


class FilterGoldCase(FrozenModel):
    """Один размеченный vacancy × target кейс на точных Processing contracts"""

    case_id: str = Field(min_length=1)
    expected_outcome: FilterOutcome
    expected_reason_codes: tuple[str, ...] = ()
    vacancy: NormalizedVacancy
    target_policy: TargetPolicy
    resume: NormalizedResume | None = None
    tags: tuple[str, ...] = ()


class FilterGoldCorpus(FrozenModel):
    """Версионированный corpus для измерения фильтра"""

    schema_version: str = "careerops.filter-gold-corpus.v1"
    corpus_id: str = Field(min_length=1)
    corpus_kind: FilterGoldCorpusKind
    release_gate_eligible: bool
    cases: tuple[FilterGoldCase, ...] = Field(min_length=1)


class FilterGoldCaseResult(FrozenModel):
    case_id: str
    expected_outcome: FilterOutcome
    actual_outcome: FilterOutcome
    actual_reason_codes: tuple[str, ...]
    false_exclusion: bool
    missed_exclusion: bool
    reason_mismatch: bool
    tags: tuple[str, ...]


class FilterGoldReport(FrozenModel):
    """Детерминированный отчёт с отдельной метрикой false exclusion"""

    schema_version: str = "careerops.filter-gold-report.v1"
    corpus_id: str
    corpus_kind: FilterGoldCorpusKind
    release_gate_eligible: bool
    total_cases: int
    expected_keep: int
    expected_exclude_proven: int
    predicted_keep: int
    predicted_exclude_proven: int
    false_exclusions: int
    missed_exclusions: int
    reason_mismatches: int
    retention_false_negative_rate: Decimal
    retention_recall: Decimal
    exclusion_precision: Decimal
    exclusion_recall: Decimal
    reason_counts: dict[str, int]
    cases: tuple[FilterGoldCaseResult, ...]


def evaluate_filter_gold(corpus: FilterGoldCorpus) -> FilterGoldReport:
    results: list[FilterGoldCaseResult] = []
    reason_counts: Counter[str] = Counter()

    for case in corpus.cases:
        decision = evaluate_filter(case.vacancy, case.target_policy, case.resume)
        reason_codes = tuple(sorted(item.reason_code for item in decision.exclusions))
        reason_counts.update(reason_codes)
        results.append(
            FilterGoldCaseResult(
                case_id=case.case_id,
                expected_outcome=case.expected_outcome,
                actual_outcome=decision.outcome,
                actual_reason_codes=reason_codes,
                false_exclusion=(
                    case.expected_outcome is FilterOutcome.KEEP
                    and decision.outcome is FilterOutcome.EXCLUDE_PROVEN
                ),
                missed_exclusion=(
                    case.expected_outcome is FilterOutcome.EXCLUDE_PROVEN
                    and decision.outcome is FilterOutcome.KEEP
                ),
                reason_mismatch=(
                    bool(case.expected_reason_codes)
                    and tuple(sorted(case.expected_reason_codes)) != reason_codes
                ),
                tags=case.tags,
            )
        )

    expected_keep = sum(
        item.expected_outcome is FilterOutcome.KEEP for item in results
    )
    expected_exclude = len(results) - expected_keep
    predicted_keep = sum(item.actual_outcome is FilterOutcome.KEEP for item in results)
    predicted_exclude = len(results) - predicted_keep
    false_exclusions = sum(item.false_exclusion for item in results)
    missed_exclusions = sum(item.missed_exclusion for item in results)
    reason_mismatches = sum(item.reason_mismatch for item in results)
    correct_exclusions = expected_exclude - missed_exclusions

    retention_fnr = (
        Decimal(false_exclusions) / Decimal(expected_keep)
        if expected_keep
        else Decimal(0)
    )
    retention_recall = Decimal(1) - retention_fnr
    exclusion_precision = (
        Decimal(correct_exclusions) / Decimal(predicted_exclude)
        if predicted_exclude
        else Decimal(1)
    )
    exclusion_recall = (
        Decimal(correct_exclusions) / Decimal(expected_exclude)
        if expected_exclude
        else Decimal(1)
    )

    return FilterGoldReport(
        corpus_id=corpus.corpus_id,
        corpus_kind=corpus.corpus_kind,
        release_gate_eligible=corpus.release_gate_eligible,
        total_cases=len(results),
        expected_keep=expected_keep,
        expected_exclude_proven=expected_exclude,
        predicted_keep=predicted_keep,
        predicted_exclude_proven=predicted_exclude,
        false_exclusions=false_exclusions,
        missed_exclusions=missed_exclusions,
        reason_mismatches=reason_mismatches,
        retention_false_negative_rate=retention_fnr,
        retention_recall=retention_recall,
        exclusion_precision=exclusion_precision,
        exclusion_recall=exclusion_recall,
        reason_counts=dict(sorted(reason_counts.items())),
        cases=tuple(results),
    )


def load_filter_gold_corpus(path: str | Path) -> FilterGoldCorpus:
    payload = Path(path).read_text(encoding="utf-8")
    return FilterGoldCorpus.model_validate_json(payload)


def write_filter_gold_report(report: FilterGoldReport, path: str | Path) -> None:
    payload = json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    Path(path).write_text(payload + "\n", encoding="utf-8")
