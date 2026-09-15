from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

import pytest

from careerops_processing.contracts import (
    JinaVersionBundle,
    MatchDecision,
    RequirementImportance,
)
from careerops_processing.contracts.common import (
    DataQualityStatus,
    RawObservationRef,
    SourceValue,
    ValueState,
)
from careerops_processing.contracts.filtering import FilterOutcome
from careerops_processing.contracts.normalized import (
    DataQualityReport,
    Employer,
    NormalizedResume,
    NormalizedVacancy,
)
from careerops_processing.contracts.policy import TargetPolicy
from careerops_processing.core import evaluate_filter, qualify_requirements, score_match
from careerops_processing.evaluation import recall_at_k, reciprocal_rank
from careerops_processing.infrastructure.reranker_http import HttpJinaRerankerClient
from careerops_processing.selector import EvidenceCandidateSelector
from careerops_reranker.config import DEFAULT_JINA_MODEL_ID, DEFAULT_JINA_REVISION
from tests.support.processing import evidence, evidence_set, requirement, requirement_set

HASH_RAW = "1" * 64
HASH_VACANCY = "2" * 64
HASH_RESUME = "3" * 64
HASH_REQUIREMENTS_REF = "4" * 64
HASH_EVIDENCE_REF = "5" * 64
HASH_CANDIDATES_REF = "6" * 64
HASH_QUALIFICATION_REF = "7" * 64
INPUT_FINGERPRINT = "8" * 64


def _known(value):
    return SourceValue(state=ValueState.KNOWN, value=value)


def _missing():
    return SourceValue(state=ValueState.NOT_PROVIDED)


def _raw_ref() -> RawObservationRef:
    return RawObservationRef(
        raw_uri="s3://careerops-ci/raw.json",
        raw_sha256=HASH_RAW,
        observed_at=datetime(2026, 9, 14, 12, tzinfo=UTC),
    )


def _dq() -> DataQualityReport:
    return DataQualityReport(
        status=DataQualityStatus.CLEAN,
        full_entity_available=True,
        parse_complete=True,
    )


def _vacancy(title: str) -> NormalizedVacancy:
    return NormalizedVacancy(
        schema_version="careerops.hh.vacancy.normalized.v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        materialization_key=f"vacancy:{title.casefold().replace(' ', '-')}",
        source_key="hh",
        source_entity_id="ci-vacancy-1",
        raw=_raw_ref(),
        semantic_content_hash=HASH_VACANCY,
        title=_known(title),
        employer=_known(Employer(name="CareerOPS CI")),
        experience=_missing(),
        location=_missing(),
        salary=_missing(),
        archived=_known(False),
        closed_for_applicants=_known(False),
        published_at=_missing(),
        dq=_dq(),
    )


def _resume() -> NormalizedResume:
    return NormalizedResume(
        schema_version="careerops.hh.resume.normalized.v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        materialization_key="resume:ci-data-engineer",
        source_key="hh",
        account_key="ci",
        source_entity_id="ci-resume-1",
        raw=_raw_ref(),
        semantic_content_hash=HASH_RESUME,
        headline=_known("Data Engineer"),
        about=_known("Python, PostgreSQL, Spark data pipelines"),
        location=_missing(),
        work_preferences=_missing(),
        relocation=_missing(),
        business_trips=_missing(),
        total_experience_years=_known(Decimal("3")),
        dq=_dq(),
    )


def _target_policy() -> TargetPolicy:
    return TargetPolicy.from_content(
        target_key="de",
        schema_version="careerops.target-policy.v1",
        policy_version="ci-baseline-v1",
        content={
            "filtering": {
                "schema_version": 1,
                "allowed_primary_roles": ["data_engineering"],
                "forbidden_primary_roles": ["java_backend"],
            },
            "scoring": {
                "schema_version": 1,
                "calibration_version": "ci-baseline-v1",
                "candidate_min_score": "50",
                "mandatory_min_support": "0.5",
                "component_weights": {
                    "mandatory_coverage": "0.8",
                    "preferred_coverage": "0.2",
                },
                "candidate_ttl_seconds": 3600,
            },
        },
    )


def _jina_version() -> JinaVersionBundle:
    return JinaVersionBundle(
        model_id=DEFAULT_JINA_MODEL_ID,
        model_revision=DEFAULT_JINA_REVISION,
        model_code_revision=DEFAULT_JINA_REVISION,
        tokenizer_revision=DEFAULT_JINA_REVISION,
        runtime_backend="transformers-cpu",
        dtype_or_quantization="float32",
        torch_version=version("torch"),
        transformers_version=version("transformers"),
        rendering_version="p205-render-v1",
        selection_version="p205-selection-v1",
        block_protocol="single-list-v1",
        token_budget=4096,
        top_k=2,
    )


def _write_report(payload: dict[str, object]) -> None:
    path = Path(os.environ.get("CAREEROPS_CI_BASELINE_REPORT", "reports/e2e-baseline.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


@pytest.mark.integration_e2e_baseline
@pytest.mark.asyncio
async def test_processing_live_jina_baseline() -> None:
    if os.environ.get("CAREEROPS_E2E_BASELINE") != "1":
        pytest.skip("live Jina baseline runs only in the dedicated CI job")

    endpoint = os.environ["CAREEROPS_TEST_RERANKER_URL"]
    thresholds = json.loads(
        Path("calibration/ci/baseline_thresholds.json").read_text(encoding="utf-8")
    )

    target = _target_policy()
    resume = _resume()
    good_vacancy = _vacancy("Data Engineer")
    skip_vacancy = _vacancy("Java Backend Developer")

    positive_filter = evaluate_filter(good_vacancy, target, resume)
    skip_filter = evaluate_filter(skip_vacancy, target, resume)

    requirements = requirement_set(
        requirement("req-python", "Python"),
        requirement("req-postgresql", "PostgreSQL"),
        requirement(
            "req-spark",
            "Spark",
            importance=RequirementImportance.PREFERRED,
        ),
    )
    evidence_items = evidence_set(
        evidence("ev-python", "Python"),
        evidence("ev-postgresql", "PostgreSQL"),
        evidence("ev-spark", "Spark"),
        evidence("ev-java", "Java"),
    )
    jina = _jina_version()

    async with HttpJinaRerankerClient(endpoint, timeout_seconds=300) as client:
        candidates = await EvidenceCandidateSelector(client).select(
            input_fingerprint=INPUT_FINGERPRINT,
            requirement_set_ref_sha256=HASH_REQUIREMENTS_REF,
            resume_evidence_set_ref_sha256=HASH_EVIDENCE_REF,
            requirement_set=requirements,
            evidence_set=evidence_items,
            jina=jina,
        )

    expected = {
        "req-python": {"ev-python"},
        "req-postgresql": {"ev-postgresql"},
        "req-spark": {"ev-spark"},
    }
    ranked_by_requirement: dict[str, tuple[str, ...]] = {}
    recalls_at_2: list[float] = []
    reciprocal_ranks: list[float] = []
    for selection in candidates.selections:
        ranked = tuple(item.evidence_id for item in selection.candidates)
        ranked_by_requirement[selection.requirement_id] = ranked
        relevant = expected[selection.requirement_id]
        recalls_at_2.append(recall_at_k(ranked, relevant, 2))
        reciprocal_ranks.append(reciprocal_rank(ranked, relevant))

    qualification = qualify_requirements(
        input_fingerprint=INPUT_FINGERPRINT,
        requirement_set_ref_sha256=HASH_REQUIREMENTS_REF,
        resume_evidence_set_ref_sha256=HASH_EVIDENCE_REF,
        evidence_candidate_set_ref_sha256=HASH_CANDIDATES_REF,
        requirement_set=requirements,
        evidence_set=evidence_items,
        candidate_set=candidates,
        qualification_version="ci-qualification-v1",
        as_of=datetime(2026, 9, 14, tzinfo=UTC).date(),
    )
    decision = score_match(
        input_fingerprint=INPUT_FINGERPRINT,
        qualification_set_ref_sha256=HASH_QUALIFICATION_REF,
        requirement_set=requirements,
        qualification_set=qualification,
        target_policy=target,
        scoring_version="ci-scoring-v1",
        calibration_version="ci-baseline-v1",
        vacancy=good_vacancy,
    )

    recall_at_2_mean = sum(recalls_at_2) / len(recalls_at_2)
    mrr = sum(reciprocal_ranks) / len(reciprocal_ranks)
    report: dict[str, object] = {
        "schema_version": "careerops.ci-e2e-baseline.v1",
        "runtime": {
            "model_id": jina.model_id,
            "model_revision": jina.model_revision,
            "runtime_backend": jina.runtime_backend,
            "dtype_or_quantization": jina.dtype_or_quantization,
            "torch_version": jina.torch_version,
            "transformers_version": jina.transformers_version,
        },
        "p203": {
            "positive_filter_outcome": positive_filter.outcome.value,
            "proven_skip_filter_outcome": skip_filter.outcome.value,
        },
        "p205": {
            "ranked_evidence": {
                key: list(value) for key, value in ranked_by_requirement.items()
            },
            "recall_at_2": recall_at_2_mean,
            "mrr": mrr,
        },
        "p206": {
            "states": {
                item.requirement_id: item.state.value for item in qualification.evaluations
            }
        },
        "p207": {
            "decision": decision.decision.value,
            "score_lower": str(decision.score.lower),
            "score_upper": str(decision.score.upper),
        },
    }
    _write_report(report)

    assert positive_filter.outcome is FilterOutcome.KEEP
    assert positive_filter.outcome.value == thresholds["positive_filter_outcome"]
    assert skip_filter.outcome is FilterOutcome.EXCLUDE_PROVEN
    assert skip_filter.outcome.value == thresholds["proven_skip_filter_outcome"]
    assert recall_at_2_mean >= float(thresholds["reranker_recall_at_2_min"])
    assert mrr >= float(thresholds["reranker_mrr_min"])
    assert decision.decision is MatchDecision.APPLICATION_CANDIDATE
    assert decision.decision.value == thresholds["positive_decision"]
