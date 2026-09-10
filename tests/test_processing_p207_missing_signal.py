from decimal import Decimal

from careerops_processing.contracts import (
    MatchDecision,
    RequirementQualificationSet,
    RequirementSet,
    TargetPolicy,
)
from careerops_processing.core import score_match

HASH_A = "a" * 64


def test_positive_weight_missing_signal_remains_uncertain() -> None:
    requirement_set = RequirementSet(
        source_key="hh",
        source_entity_id="vacancy-1",
        semantic_content_hash=HASH_A,
        normalized_schema_version="vacancy-v1",
        normalization_version="normalizer-v1",
        dictionary_version="dict-v1",
        extraction_version="requirements-v2",
    )
    qualification_set = RequirementQualificationSet(
        input_fingerprint=HASH_A,
        requirement_set_sha256=HASH_A,
        resume_evidence_set_sha256=HASH_A,
        evidence_candidate_set_sha256=HASH_A,
        qualification_version="qualification-v1",
    )
    policy = TargetPolicy.from_content(
        target_key="de",
        schema_version="target-policy-v1",
        policy_version="policy-v1",
        content={
            "filtering": {"schema_version": 1},
            "scoring": {
                "schema_version": 1,
                "calibration_version": "gold-v1",
                "candidate_min_score": "80",
                "mandatory_min_support": "1",
                "component_weights": {"role_fit": "1"},
                "candidate_ttl_seconds": 3600,
            },
        },
    )

    decision = score_match(
        input_fingerprint=HASH_A,
        qualification_set_ref_sha256=HASH_A,
        requirement_set=requirement_set,
        qualification_set=qualification_set,
        target_policy=policy,
        scoring_version="scoring-v1",
        calibration_version="gold-v1",
        vacancy=None,
    )

    assert decision.decision is MatchDecision.REVIEW
    assert decision.score.lower == Decimal("0")
    assert decision.score.upper == Decimal("100")
    assert decision.components[0].key == "role_fit"
    assert decision.components[0].weight == Decimal("1")
