from decimal import Decimal

import pytest
from pydantic import ValidationError

from careerops_processing.contracts import (
    RequirementQualification,
    RequirementQualificationState,
    SupportBounds,
)


def _bounds(lower: str, upper: str) -> SupportBounds:
    return SupportBounds(lower=Decimal(lower), upper=Decimal(upper))


def test_qualification_semantic_evidence_must_be_ranked() -> None:
    with pytest.raises(ValidationError, match="must belong to ranked evidence"):
        RequirementQualification(
            requirement_id="req-python",
            state=RequirementQualificationState.MATCHED,
            support=_bounds("1", "1"),
            selection_complete=True,
            ranked_evidence_ids=("ev-ranked",),
            supporting_evidence_ids=("ev-not-ranked",),
            reason_codes=("requirements.direct_support",),
        )


def test_qualification_semantic_evidence_roles_must_be_disjoint() -> None:
    with pytest.raises(ValidationError, match="must be disjoint"):
        RequirementQualification(
            requirement_id="req-python",
            state=RequirementQualificationState.UNKNOWN,
            support=_bounds("0", "1"),
            selection_complete=True,
            ranked_evidence_ids=("ev-conflict",),
            supporting_evidence_ids=("ev-conflict",),
            contradicting_evidence_ids=("ev-conflict",),
            reason_codes=("requirements.source_conflict",),
        )


def test_matched_cannot_carry_contradicting_evidence() -> None:
    with pytest.raises(ValidationError, match="MATCHED cannot contain contradicting"):
        RequirementQualification(
            requirement_id="req-python",
            state=RequirementQualificationState.MATCHED,
            support=_bounds("1", "1"),
            selection_complete=True,
            ranked_evidence_ids=("ev-support", "ev-contradiction"),
            supporting_evidence_ids=("ev-support",),
            contradicting_evidence_ids=("ev-contradiction",),
            reason_codes=("requirements.direct_support",),
        )
