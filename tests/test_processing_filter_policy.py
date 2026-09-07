from __future__ import annotations

import pytest
from pydantic import ValidationError

from careerops_processing.contracts.filtering import FilterPolicy, RoleFamily
from careerops_processing.contracts.policy import TargetPolicy


def target_policy(content: dict) -> TargetPolicy:
    return TargetPolicy.from_content(
        target_key="test",
        schema_version="careerops.target-policy.v1",
        policy_version="filter-policy-v1",
        content=content,
    )


def test_missing_filtering_section_means_no_admission_restrictions() -> None:
    parsed = FilterPolicy.from_target_policy(target_policy({}))
    assert parsed.allowed_primary_roles == ()
    assert parsed.forbidden_primary_roles == ()
    assert parsed.management_allowed is True


def test_filter_policy_json_deserializes_typed_enums_and_decimal() -> None:
    parsed = FilterPolicy.from_target_policy(
        target_policy(
            {
                "filtering": {
                    "schema_version": 1,
                    "allowed_primary_roles": ["data_engineering"],
                    "maximum_experience_gap_years": 1.5,
                }
            }
        )
    )
    assert parsed.allowed_primary_roles == (RoleFamily.DATA_ENGINEERING,)
    assert str(parsed.maximum_experience_gap_years) == "1.5"


def test_role_cannot_be_both_allowed_and_forbidden() -> None:
    policy = target_policy(
        {
            "filtering": {
                "schema_version": 1,
                "allowed_primary_roles": ["data_engineering"],
                "forbidden_primary_roles": ["data_engineering"],
            }
        }
    )
    with pytest.raises(ValidationError, match="both allowed and forbidden"):
        FilterPolicy.from_target_policy(policy)
