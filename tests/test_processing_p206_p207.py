from __future__ import annotations

from datetime import date
from decimal import Decimal

from careerops_processing.contracts import (
    EvidenceActorScope,
    EvidenceCandidate,
    EvidenceCandidateSet,
    EvidenceContext,
    EvidenceKind,
    EvidenceStrength,
    JinaVersionBundle,
    MatchDecision,
    Requirement,
    RequirementContext,
    RequirementEvidenceCandidates,
    RequirementGroup,
    RequirementGroupOperator,
    RequirementImportance,
    RequirementKind,
    RequirementModality,
    RequirementQualificationState,
    RequirementSelectionState,
    RequirementSet,
    RequirementThreshold,
    RequirementThresholdMetric,
    ResumeEvidence,
    ResumeEvidenceSet,
    SemanticPolarity,
    SemanticSourceRef,
    SemanticSubject,
    SemanticTimeSpan,
    TargetPolicy,
)
from careerops_processing.core import qualify_requirements, score_match

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _source(path: str) -> SemanticSourceRef:
    return SemanticSourceRef(source_path=path, rendered_value="source")


def _importance_modality(
    importance: RequirementImportance,
) -> RequirementModality:
    return {
        RequirementImportance.MANDATORY: RequirementModality.REQUIRED,
        RequirementImportance.PREFERRED: RequirementModality.PREFERRED,
        RequirementImportance.OPTIONAL: RequirementModality.OPTIONAL,
        RequirementImportance.UNKNOWN: RequirementModality.UNKNOWN,
    }[importance]


def _requirement(
    requirement_id: str,
    subject: str,
    *,
    importance: RequirementImportance = RequirementImportance.MANDATORY,
    threshold: RequirementThreshold | None = None,
) -> Requirement:
    return Requirement(
        requirement_id=requirement_id,
        kind=RequirementKind.TECHNOLOGY,
        statement=f"Требуется {subject}",
        subjects=(SemanticSubject(text=subject, normalized=subject.casefold()),),
        context=RequirementContext.QUALIFICATION,
        importance=importance,
        modality=_importance_modality(importance),
        polarity=SemanticPolarity.POSITIVE,
        threshold=threshold,
        source_refs=(_source(f"requirements.{requirement_id}"),),
    )


def _prohibited_requirement(requirement_id: str, subject: str) -> Requirement:
    return Requirement(
        requirement_id=requirement_id,
        kind=RequirementKind.TECHNOLOGY,
        statement=f"Запрещён {subject}",
        subjects=(SemanticSubject(text=subject, normalized=subject.casefold()),),
        context=RequirementContext.QUALIFICATION,
        importance=RequirementImportance.MANDATORY,
        modality=RequirementModality.PROHIBITED,
        polarity=SemanticPolarity.NEGATIVE,
        source_refs=(_source(f"requirements.{requirement_id}"),),
    )


def _requirement_set(
    *requirements: Requirement,
    groups: tuple[RequirementGroup, ...] | None = None,
    root_group_id: str = "root",
) -> RequirementSet:
    resolved_groups = groups or (
        RequirementGroup(
            group_id="root",
            operator=RequirementGroupOperator.ALL,
            requirement_ids=tuple(item.requirement_id for item in requirements),
        ),
    )
    return RequirementSet(
        source_key="hh",
        source_entity_id="vacancy-1",
        semantic_content_hash=HASH_A,
        normalized_schema_version="vacancy-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        extraction_version="requirements-v2",
        requirements=tuple(requirements),
        groups=resolved_groups,
        root_group_id=root_group_id,
    )


def _evidence(
    evidence_id: str,
    subject: str,
    *,
    polarity: SemanticPolarity = SemanticPolarity.POSITIVE,
    actor_scope: EvidenceActorScope = EvidenceActorScope.SELF,
    strength: EvidenceStrength = EvidenceStrength.DIRECT,
    time_span: SemanticTimeSpan | None = None,
) -> ResumeEvidence:
    return ResumeEvidence(
        evidence_id=evidence_id,
        kind=EvidenceKind.EXPERIENCE,
        statement=f"Evidence {subject}",
        subjects=(SemanticSubject(text=subject, normalized=subject.casefold()),),
        actor_scope=actor_scope,
        context=EvidenceContext.COMMERCIAL,
        polarity=polarity,
        strength=strength,
        time_span=time_span,
        source_refs=(_source(f"evidence.{evidence_id}"),),
    )


def _evidence_set(*evidence: ResumeEvidence) -> ResumeEvidenceSet:
    return ResumeEvidenceSet(
        source_key="hh",
        account_key="account-1",
        source_entity_id="resume-1",
        semantic_content_hash=HASH_B,
        normalized_schema_version="resume-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        evidence_version="evidence-v2",
        evidence=tuple(evidence),
    )


def _jina() -> JinaVersionBundle:
    return JinaVersionBundle(
        model_id="jinaai/jina-reranker-v3.5",
        model_revision="model-rev",
        model_code_revision="code-rev",
        tokenizer_revision="tokenizer-rev",
        runtime_backend="transformers-cuda",
        dtype_or_quantization="float16",
        torch_version="2.14.0",
        transformers_version="4.57.3",
        rendering_version="p205-render-v1",
        selection_version="p205-selection-v1",
        block_protocol="single-list-v1",
        token_budget=4096,
        top_k=3,
    )


def _candidates(
    requirement_set: RequirementSet,
    evidence_set: ResumeEvidenceSet,
    *,
    scores: dict[str, float] | None = None,
    incomplete_pool_for: str | None = None,
) -> EvidenceCandidateSet:
    selections: list[RequirementEvidenceCandidates] = []
    evidence_ids = tuple(item.evidence_id for item in evidence_set.evidence)
    score_map = scores or {}
    for requirement in requirement_set.requirements:
        if requirement.modality is RequirementModality.NOT_REQUIRED:
            selections.append(
                RequirementEvidenceCandidates(
                    requirement_id=requirement.requirement_id,
                    state=RequirementSelectionState.SKIPPED_NOT_REQUIRED,
                )
            )
            continue
        if not evidence_ids:
            selections.append(
                RequirementEvidenceCandidates(
                    requirement_id=requirement.requirement_id,
                    state=RequirementSelectionState.NO_EVIDENCE,
                    query_text=requirement.statement,
                )
            )
            continue
        pool = evidence_ids
        if incomplete_pool_for == requirement.requirement_id:
            pool = evidence_ids[:1]
        selections.append(
            RequirementEvidenceCandidates(
                requirement_id=requirement.requirement_id,
                state=RequirementSelectionState.RANKED,
                query_text=requirement.statement,
                pool_evidence_ids=pool,
                pool_render_sha256=HASH_D,
                candidates=tuple(
                    EvidenceCandidate(
                        evidence_id=evidence_id,
                        rank=index,
                        relevance_score=score_map.get(evidence_id, 0.5),
                    )
                    for index, evidence_id in enumerate(pool, start=1)
                ),
            )
        )
    return EvidenceCandidateSet(
        input_fingerprint=HASH_C,
        requirement_set_sha256=HASH_A,
        resume_evidence_set_sha256=HASH_B,
        jina=_jina(),
        selections=tuple(selections),
    )


def _qualify(
    requirement_set: RequirementSet,
    evidence_set: ResumeEvidenceSet,
    candidates: EvidenceCandidateSet,
):
    return qualify_requirements(
        input_fingerprint=HASH_C,
        requirement_set_ref_sha256=HASH_A,
        resume_evidence_set_ref_sha256=HASH_B,
        evidence_candidate_set_ref_sha256=HASH_D,
        requirement_set=requirement_set,
        evidence_set=evidence_set,
        candidate_set=candidates,
        qualification_version="qualification-v1",
        as_of=date(2026, 9, 10),
    )


def _policy(*, calibrated: bool, threshold: str = "80") -> TargetPolicy:
    content: dict[str, object] = {"filtering": {"schema_version": 1}}
    if calibrated:
        content["scoring"] = {
            "schema_version": 1,
            "calibration_version": "gold-v1",
            "candidate_min_score": threshold,
            "mandatory_min_support": "1",
            "component_weights": {"mandatory_coverage": "1"},
            "candidate_ttl_seconds": 3600,
        }
    return TargetPolicy.from_content(
        target_key="de",
        schema_version="target-policy-v1",
        policy_version="policy-v1",
        content=content,
    )


def _score(
    requirement_set: RequirementSet,
    qualification_set,
    *,
    calibrated: bool,
    threshold: str = "80",
):
    return score_match(
        input_fingerprint=HASH_C,
        qualification_set_ref_sha256=HASH_D,
        requirement_set=requirement_set,
        qualification_set=qualification_set,
        target_policy=_policy(calibrated=calibrated, threshold=threshold),
        scoring_version="scoring-v1",
        calibration_version="gold-v1" if calibrated else "calibration-unset",
    )


def test_low_jina_score_does_not_block_direct_match() -> None:
    requirements = _requirement_set(_requirement("req-python", "Python"))
    evidence = _evidence_set(_evidence("ev-python", "Python"))
    candidates = _candidates(requirements, evidence, scores={"ev-python": 0.01})

    result = _qualify(requirements, evidence, candidates)

    assert result.evaluations[0].state is RequirementQualificationState.MATCHED
    assert result.evaluations[0].support.lower == Decimal("1")


def test_empty_evidence_is_not_evidenced_not_contradicted() -> None:
    requirements = _requirement_set(_requirement("req-python", "Python"))
    evidence = _evidence_set()

    result = _qualify(requirements, evidence, _candidates(requirements, evidence))

    assert result.evaluations[0].state is RequirementQualificationState.NOT_EVIDENCED
    assert result.evaluations[0].support.lower == Decimal("0")
    assert result.evaluations[0].support.upper == Decimal("1")


def test_team_mention_remains_unknown() -> None:
    requirements = _requirement_set(_requirement("req-k8s", "Kubernetes"))
    evidence = _evidence_set(
        _evidence("ev-k8s-team", "Kubernetes", actor_scope=EvidenceActorScope.TEAM)
    )

    result = _qualify(requirements, evidence, _candidates(requirements, evidence))

    assert result.evaluations[0].state is RequirementQualificationState.UNKNOWN


def test_explicit_negative_self_evidence_is_contradiction() -> None:
    requirements = _requirement_set(_requirement("req-k8s", "Kubernetes"))
    evidence = _evidence_set(
        _evidence("ev-k8s-negative", "Kubernetes", polarity=SemanticPolarity.NEGATIVE)
    )

    result = _qualify(requirements, evidence, _candidates(requirements, evidence))

    assert result.evaluations[0].state is RequirementQualificationState.CONTRADICTED


def test_conflicting_positive_and_negative_sources_are_unknown() -> None:
    requirements = _requirement_set(_requirement("req-k8s", "Kubernetes"))
    evidence = _evidence_set(
        _evidence("ev-positive", "Kubernetes"),
        _evidence("ev-negative", "Kubernetes", polarity=SemanticPolarity.NEGATIVE),
    )

    result = _qualify(requirements, evidence, _candidates(requirements, evidence))

    assert result.evaluations[0].state is RequirementQualificationState.UNKNOWN
    assert result.evaluations[0].support.lower == Decimal("0")
    assert result.evaluations[0].support.upper == Decimal("1")


def test_incomplete_selection_can_never_prove_not_evidenced() -> None:
    requirements = _requirement_set(_requirement("req-rust", "Rust"))
    evidence = _evidence_set(_evidence("ev-python", "Python"), _evidence("ev-sql", "SQL"))
    candidates = _candidates(requirements, evidence, incomplete_pool_for="req-rust")

    result = _qualify(requirements, evidence, candidates)

    assert result.evaluations[0].state is RequirementQualificationState.UNKNOWN
    assert result.evaluations[0].selection_complete is False


def test_experience_threshold_uses_evidenced_duration_without_contradiction() -> None:
    requirement = _requirement(
        "req-python-years",
        "Python",
        threshold=RequirementThreshold(
            metric=RequirementThresholdMetric.EXPERIENCE_YEARS,
            minimum=Decimal("3"),
        ),
    )
    requirements = _requirement_set(requirement)
    evidence = _evidence_set(
        _evidence(
            "ev-python",
            "Python",
            time_span=SemanticTimeSpan(
                start_date=date(2024, 1, 1),
                end_date=date(2025, 1, 1),
                currently_active=False,
            ),
        )
    )

    result = _qualify(requirements, evidence, _candidates(requirements, evidence))

    assert result.evaluations[0].state is RequirementQualificationState.NOT_EVIDENCED


def test_any_group_uses_max_support_and_is_one_scoring_unit() -> None:
    python = _requirement("req-python", "Python")
    scala = _requirement("req-scala", "Scala")
    sql = _requirement("req-sql", "SQL")
    groups = (
        RequirementGroup(
            group_id="root",
            operator=RequirementGroupOperator.ALL,
            requirement_ids=("req-sql",),
            child_group_ids=("language",),
        ),
        RequirementGroup(
            group_id="language",
            operator=RequirementGroupOperator.ANY,
            requirement_ids=("req-python", "req-scala"),
        ),
    )
    requirements = _requirement_set(python, scala, sql, groups=groups)
    evidence = _evidence_set(_evidence("ev-python", "Python"), _evidence("ev-sql", "SQL"))
    qualification = _qualify(requirements, evidence, _candidates(requirements, evidence))

    language = next(item for item in qualification.groups if item.group_id == "language")
    assert language.support.lower == Decimal("1")

    decision = _score(requirements, qualification, calibrated=True)
    mandatory = next(item for item in decision.components if item.key == "mandatory_coverage")
    assert mandatory.lower == Decimal("100")
    assert decision.decision is MatchDecision.APPLICATION_CANDIDATE


def test_required_mandatory_contradiction_uses_calibrated_policy_not_critical_gate() -> None:
    mandatory = _requirement("req-k8s", "Kubernetes")
    optional = _requirement(
        "req-python",
        "Python",
        importance=RequirementImportance.OPTIONAL,
    )
    requirements = _requirement_set(mandatory, optional)
    evidence = _evidence_set(
        _evidence("ev-k8s-negative", "Kubernetes", polarity=SemanticPolarity.NEGATIVE),
        _evidence("ev-python", "Python"),
    )
    qualification = _qualify(requirements, evidence, _candidates(requirements, evidence))

    decision = _score(requirements, qualification, calibrated=True)

    assert decision.decision is MatchDecision.SKIP
    assert decision.deterministic_score == Decimal("0")
    assert decision.critical_conflict_requirement_ids == ()
    assert decision.reason_codes == ("match.policy_not_satisfied",)


def test_required_mandatory_contradiction_stays_review_before_calibration() -> None:
    requirements = _requirement_set(_requirement("req-k8s", "Kubernetes"))
    evidence = _evidence_set(
        _evidence("ev-k8s-negative", "Kubernetes", polarity=SemanticPolarity.NEGATIVE)
    )
    qualification = _qualify(requirements, evidence, _candidates(requirements, evidence))

    decision = _score(requirements, qualification, calibrated=False)

    assert decision.decision is MatchDecision.REVIEW
    assert decision.critical_conflict_requirement_ids == ()
    assert decision.reason_codes == ("match.calibration_unset",)


def test_prohibited_contradiction_is_non_compensable_hard_skip() -> None:
    prohibited = _prohibited_requirement("req-windows", "Windows")
    optional = _requirement(
        "req-python",
        "Python",
        importance=RequirementImportance.OPTIONAL,
    )
    requirements = _requirement_set(prohibited, optional)
    evidence = _evidence_set(
        _evidence("ev-windows", "Windows", polarity=SemanticPolarity.POSITIVE),
        _evidence("ev-python", "Python"),
    )
    qualification = _qualify(requirements, evidence, _candidates(requirements, evidence))

    decision = _score(requirements, qualification, calibrated=False)

    assert decision.decision is MatchDecision.SKIP
    assert decision.deterministic_score == Decimal("0")
    assert decision.critical_conflict_requirement_ids == ("req-windows",)
    assert decision.reason_codes == ("requirements.critical_contradiction",)


def test_unset_calibration_never_auto_publishes_application_candidate() -> None:
    requirements = _requirement_set(_requirement("req-python", "Python"))
    evidence = _evidence_set(_evidence("ev-python", "Python"))
    qualification = _qualify(requirements, evidence, _candidates(requirements, evidence))

    decision = _score(requirements, qualification, calibrated=False)

    assert decision.decision is MatchDecision.REVIEW
    assert decision.reason_codes == ("match.calibration_unset",)


def test_calibrated_bounds_select_candidate_skip_or_review() -> None:
    matched_requirements = _requirement_set(_requirement("req-python", "Python"))
    matched_evidence = _evidence_set(_evidence("ev-python", "Python"))
    matched = _qualify(
        matched_requirements,
        matched_evidence,
        _candidates(matched_requirements, matched_evidence),
    )
    matched_decision = _score(matched_requirements, matched, calibrated=True)
    assert matched_decision.decision is MatchDecision.APPLICATION_CANDIDATE

    preferred = _requirement(
        "req-python",
        "Python",
        importance=RequirementImportance.PREFERRED,
    )
    preferred_requirements = _requirement_set(preferred)
    negative_evidence = _evidence_set(
        _evidence("ev-python-negative", "Python", polarity=SemanticPolarity.NEGATIVE)
    )
    negative = _qualify(
        preferred_requirements,
        negative_evidence,
        _candidates(preferred_requirements, negative_evidence),
    )
    skip_policy = TargetPolicy.from_content(
        target_key="de",
        schema_version="target-policy-v1",
        policy_version="policy-v1",
        content={
            "filtering": {"schema_version": 1},
            "scoring": {
                "schema_version": 1,
                "calibration_version": "gold-v1",
                "candidate_min_score": "80",
                "mandatory_min_support": "0",
                "component_weights": {"preferred_coverage": "1"},
                "candidate_ttl_seconds": 3600,
            },
        },
    )
    skipped = score_match(
        input_fingerprint=HASH_C,
        qualification_set_ref_sha256=HASH_D,
        requirement_set=preferred_requirements,
        qualification_set=negative,
        target_policy=skip_policy,
        scoring_version="scoring-v1",
        calibration_version="gold-v1",
    )
    assert skipped.decision is MatchDecision.SKIP

    absent_evidence = _evidence_set()
    uncertain = _qualify(
        preferred_requirements,
        absent_evidence,
        _candidates(preferred_requirements, absent_evidence),
    )
    review = score_match(
        input_fingerprint=HASH_C,
        qualification_set_ref_sha256=HASH_D,
        requirement_set=preferred_requirements,
        qualification_set=uncertain,
        target_policy=skip_policy,
        scoring_version="scoring-v1",
        calibration_version="gold-v1",
    )
    assert review.score.lower == Decimal("0")
    assert review.score.upper == Decimal("100")
    assert review.decision is MatchDecision.REVIEW
