"""Pure deterministic P2-07 scoring, policy gates and decision bounds."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from careerops_processing.contracts.common import ValueState
from careerops_processing.contracts.filtering import FilterPolicy, WorkFormat
from careerops_processing.contracts.normalized import NormalizedVacancy
from careerops_processing.contracts.policy import TargetPolicy
from careerops_processing.contracts.qualification import (
    RequirementQualificationSet,
    RequirementQualificationState,
    SupportBounds,
)
from careerops_processing.contracts.requirements import (
    RequirementGroup,
    RequirementGroupOperator,
    RequirementImportance,
    RequirementKind,
    RequirementModality,
    RequirementSet,
)
from careerops_processing.contracts.scoring import (
    MatchDecision,
    MatchDecisionBundle,
    ScoreBounds,
    ScoringComponent,
    ScoringPolicy,
)

from .filtering import (
    _known_title,
    _known_work_formats,
    _normalize,
    _role_families,
    _seniority_levels,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class _ScoringUnit:
    requirement_ids: tuple[str, ...]
    support: SupportBounds
    importance: RequirementImportance
    kind: RequirementKind


def _leaf_requirement_ids(
    group: RequirementGroup,
    *,
    group_by_id: dict[str, RequirementGroup],
) -> tuple[str, ...]:
    values = list(group.requirement_ids)
    for child_group_id in group.child_group_ids:
        values.extend(
            _leaf_requirement_ids(group_by_id[child_group_id], group_by_id=group_by_id)
        )
    return tuple(values)


def _derived_importance(values: tuple[RequirementImportance, ...]) -> RequirementImportance:
    unique = set(values)
    if len(unique) == 1:
        return values[0]
    return RequirementImportance.UNKNOWN


def _derived_kind(values: tuple[RequirementKind, ...]) -> RequirementKind:
    unique = set(values)
    if len(unique) == 1:
        return values[0]
    return RequirementKind.OTHER


def _scoring_units(
    requirement_set: RequirementSet,
    qualification_set: RequirementQualificationSet,
) -> tuple[_ScoringUnit, ...]:
    if requirement_set.root_group_id is None:
        return ()

    requirement_by_id = {item.requirement_id: item for item in requirement_set.requirements}
    group_by_id = {item.group_id: item for item in requirement_set.groups}
    group_support = {item.group_id: item.support for item in qualification_set.groups}
    evaluation_support = {
        item.requirement_id: item.support for item in qualification_set.evaluations
    }
    ignored = set(qualification_set.ignored_requirement_ids)

    def requirement_unit(requirement_id: str) -> _ScoringUnit | None:
        if requirement_id in ignored:
            return None
        requirement = requirement_by_id[requirement_id]
        return _ScoringUnit(
            requirement_ids=(requirement_id,),
            support=evaluation_support[requirement_id],
            importance=requirement.importance,
            kind=requirement.kind,
        )

    def composite_unit(group: RequirementGroup) -> _ScoringUnit | None:
        leaves = tuple(
            item
            for item in _leaf_requirement_ids(group, group_by_id=group_by_id)
            if item not in ignored
        )
        if not leaves:
            return None
        importances = tuple(requirement_by_id[item].importance for item in leaves)
        kinds = tuple(requirement_by_id[item].kind for item in leaves)
        return _ScoringUnit(
            requirement_ids=leaves,
            support=group_support[group.group_id],
            importance=_derived_importance(importances),
            kind=_derived_kind(kinds),
        )

    def collect(group: RequirementGroup) -> list[_ScoringUnit]:
        if group.operator is not RequirementGroupOperator.ALL:
            unit = composite_unit(group)
            return [] if unit is None else [unit]

        result: list[_ScoringUnit] = []
        for requirement_id in group.requirement_ids:
            unit = requirement_unit(requirement_id)
            if unit is not None:
                result.append(unit)
        for child_group_id in group.child_group_ids:
            result.extend(collect(group_by_id[child_group_id]))
        return result

    return tuple(collect(group_by_id[requirement_set.root_group_id]))


def _component_bounds(units: tuple[_ScoringUnit, ...]) -> tuple[Decimal, Decimal] | None:
    if not units:
        return None
    count = Decimal(len(units))
    lower = sum((item.support.lower for item in units), _ZERO) / count * _HUNDRED
    upper = sum((item.support.upper for item in units), _ZERO) / count * _HUNDRED
    return lower, upper


def _component_units(
    units: tuple[_ScoringUnit, ...],
) -> dict[str, tuple[_ScoringUnit, ...]]:
    return {
        "mandatory_coverage": tuple(
            item for item in units if item.importance is RequirementImportance.MANDATORY
        ),
        "preferred_coverage": tuple(
            item for item in units if item.importance is RequirementImportance.PREFERRED
        ),
        "optional_coverage": tuple(
            item for item in units if item.importance is RequirementImportance.OPTIONAL
        ),
        "technology_fit": tuple(
            item for item in units if item.kind is RequirementKind.TECHNOLOGY
        ),
        "responsibility_fit": tuple(
            item for item in units if item.kind is RequirementKind.RESPONSIBILITY
        ),
        "experience_fit": tuple(
            item for item in units if item.kind is RequirementKind.EXPERIENCE
        ),
        "domain_fit": tuple(item for item in units if item.kind is RequirementKind.DOMAIN),
        "education_fit": tuple(
            item for item in units if item.kind is RequirementKind.EDUCATION
        ),
        "language_fit": tuple(
            item for item in units if item.kind is RequirementKind.LANGUAGE
        ),
        "work_condition_fit": tuple(
            item for item in units if item.kind is RequirementKind.WORK_CONDITION
        ),
        "other_fit": tuple(item for item in units if item.kind is RequirementKind.OTHER),
    }


def _uncertain() -> tuple[Decimal, Decimal]:
    return _ZERO, _HUNDRED


def _structured_component_bounds(
    vacancy: NormalizedVacancy,
    target_policy: TargetPolicy,
) -> dict[str, tuple[Decimal, Decimal]]:
    """Bound structured compatibility without treating admission KEEP as a positive match."""

    policy = FilterPolicy.from_target_policy(target_policy)
    result: dict[str, tuple[Decimal, Decimal]] = {}
    title = _known_title(vacancy)

    if policy.allowed_primary_roles or policy.forbidden_primary_roles:
        if title is None:
            result["role_fit"] = _uncertain()
        else:
            families = _role_families(title)
            allowed_roles = set(policy.allowed_primary_roles)
            forbidden_roles = set(policy.forbidden_primary_roles)
            if not families:
                result["role_fit"] = _uncertain()
            elif allowed_roles:
                if families <= allowed_roles:
                    result["role_fit"] = (_HUNDRED, _HUNDRED)
                elif families.isdisjoint(allowed_roles):
                    result["role_fit"] = (_ZERO, _ZERO)
                else:
                    result["role_fit"] = _uncertain()
            elif families <= forbidden_roles:
                result["role_fit"] = (_ZERO, _ZERO)
            elif families.isdisjoint(forbidden_roles):
                result["role_fit"] = (_HUNDRED, _HUNDRED)
            else:
                result["role_fit"] = _uncertain()

    if policy.forbidden_seniority:
        if title is None:
            result["seniority_fit"] = _uncertain()
        else:
            levels = _seniority_levels(title)
            forbidden_seniority = set(policy.forbidden_seniority)
            if not levels:
                result["seniority_fit"] = _uncertain()
            elif levels <= forbidden_seniority:
                result["seniority_fit"] = (_ZERO, _ZERO)
            elif levels.isdisjoint(forbidden_seniority):
                result["seniority_fit"] = (_HUNDRED, _HUNDRED)
            else:
                result["seniority_fit"] = _uncertain()

    formats, all_formats_understood = _known_work_formats(vacancy)
    if policy.allowed_work_formats:
        allowed_formats = set(policy.allowed_work_formats)
        if formats & allowed_formats:
            result["work_format_fit"] = (_HUNDRED, _HUNDRED)
        elif formats and all_formats_understood:
            result["work_format_fit"] = (_ZERO, _ZERO)
        else:
            result["work_format_fit"] = _uncertain()

    if policy.allowed_area_ids or policy.allowed_area_names:
        if WorkFormat.REMOTE in formats:
            result["location_fit"] = (_HUNDRED, _HUNDRED)
        elif not all_formats_understood:
            result["location_fit"] = _uncertain()
        elif vacancy.location.state is not ValueState.KNOWN or vacancy.location.value is None:
            result["location_fit"] = _uncertain()
        else:
            location = vacancy.location.value
            actual_id = location.area_id.casefold() if location.area_id else None
            actual_name = _normalize(location.area_name) if location.area_name else None
            matched = False
            complete = True
            if policy.allowed_area_ids:
                if actual_id is None:
                    complete = False
                else:
                    allowed_ids = {value.casefold() for value in policy.allowed_area_ids}
                    matched = matched or actual_id in allowed_ids
            if policy.allowed_area_names:
                if actual_name is None:
                    complete = False
                else:
                    allowed_names = {_normalize(value) for value in policy.allowed_area_names}
                    matched = matched or actual_name in allowed_names
            if matched:
                result["location_fit"] = (_HUNDRED, _HUNDRED)
            elif complete:
                result["location_fit"] = (_ZERO, _ZERO)
            else:
                result["location_fit"] = _uncertain()

    return result


def _build_components(
    component_units: dict[str, tuple[_ScoringUnit, ...]],
    policy: ScoringPolicy | None,
    structured_bounds: dict[str, tuple[Decimal, Decimal]],
) -> tuple[ScoringComponent, ...]:
    values: list[ScoringComponent] = []
    for key, units in component_units.items():
        bounds = _component_bounds(units)
        if bounds is None:
            continue
        weight = _ZERO if policy is None else policy.component_weights.get(key, _ZERO)
        values.append(
            ScoringComponent(
                key=key,
                lower=bounds[0],
                upper=bounds[1],
                weight=weight,
            )
        )
    for key, bounds in structured_bounds.items():
        weight = _ZERO if policy is None else policy.component_weights.get(key, _ZERO)
        values.append(
            ScoringComponent(key=key, lower=bounds[0], upper=bounds[1], weight=weight)
        )

    if policy is not None:
        present_keys = {item.key for item in values}
        for key, weight in policy.component_weights.items():
            if weight > 0 and key not in present_keys:
                values.append(
                    ScoringComponent(
                        key=key,
                        lower=_ZERO,
                        upper=_HUNDRED,
                        weight=weight,
                    )
                )
    return tuple(values)


def _weighted_score(
    components: tuple[ScoringComponent, ...],
) -> ScoreBounds | None:
    active = tuple(item for item in components if item.weight > 0)
    if not active:
        return None
    weight_sum = sum((item.weight for item in active), _ZERO)
    lower = sum((item.lower * item.weight for item in active), _ZERO) / weight_sum
    upper = sum((item.upper * item.weight for item in active), _ZERO) / weight_sum
    return ScoreBounds(lower=lower, upper=upper)


def _mandatory_support(units: tuple[_ScoringUnit, ...]) -> SupportBounds:
    mandatory = tuple(
        item for item in units if item.importance is RequirementImportance.MANDATORY
    )
    if not mandatory:
        return SupportBounds(lower=_ONE, upper=_ONE)
    count = Decimal(len(mandatory))
    return SupportBounds(
        lower=sum((item.support.lower for item in mandatory), _ZERO) / count,
        upper=sum((item.support.upper for item in mandatory), _ZERO) / count,
    )


def score_match(
    *,
    input_fingerprint: str,
    qualification_set_ref_sha256: str,
    requirement_set: RequirementSet,
    qualification_set: RequirementQualificationSet,
    target_policy: TargetPolicy,
    scoring_version: str,
    calibration_version: str,
    vacancy: NormalizedVacancy | None = None,
) -> MatchDecisionBundle:
    """Produce a replayable bounded decision without LLM calls or Jina score thresholds."""

    qualification_by_id = {
        item.requirement_id: item for item in qualification_set.evaluations
    }
    requirement_by_id = {item.requirement_id: item for item in requirement_set.requirements}

    critical_conflicts = tuple(
        requirement_id
        for requirement_id, evaluation in qualification_by_id.items()
        if evaluation.state is RequirementQualificationState.CONTRADICTED
        and requirement_by_id[requirement_id].modality is RequirementModality.PROHIBITED
    )
    unknown_ids = tuple(
        item.requirement_id
        for item in qualification_set.evaluations
        if item.state is RequirementQualificationState.UNKNOWN
    )
    not_evidenced_ids = tuple(
        item.requirement_id
        for item in qualification_set.evaluations
        if item.state is RequirementQualificationState.NOT_EVIDENCED
    )

    units = _scoring_units(requirement_set, qualification_set)
    component_units = _component_units(units)
    mandatory_support = _mandatory_support(units)
    structured_bounds = (
        {} if vacancy is None else _structured_component_bounds(vacancy, target_policy)
    )

    if critical_conflicts:
        components = _build_components(component_units, None, structured_bounds)
        return MatchDecisionBundle(
            input_fingerprint=input_fingerprint,
            requirement_qualification_set_sha256=qualification_set_ref_sha256,
            scoring_version=scoring_version,
            calibration_version=calibration_version,
            policy_version=target_policy.policy_version,
            decision=MatchDecision.SKIP,
            score=ScoreBounds(lower=_ZERO, upper=_ZERO),
            deterministic_score=_ZERO,
            components=components,
            critical_conflict_requirement_ids=critical_conflicts,
            unknown_requirement_ids=unknown_ids,
            not_evidenced_requirement_ids=not_evidenced_ids,
            reason_codes=("requirements.critical_contradiction",),
        )

    if calibration_version == "calibration-unset":
        mandatory_score = ScoreBounds(
            lower=mandatory_support.lower * _HUNDRED,
            upper=mandatory_support.upper * _HUNDRED,
        )
        return MatchDecisionBundle(
            input_fingerprint=input_fingerprint,
            requirement_qualification_set_sha256=qualification_set_ref_sha256,
            scoring_version=scoring_version,
            calibration_version=calibration_version,
            policy_version=target_policy.policy_version,
            decision=MatchDecision.REVIEW,
            score=mandatory_score,
            deterministic_score=mandatory_score.lower,
            components=_build_components(component_units, None, structured_bounds),
            unknown_requirement_ids=unknown_ids,
            not_evidenced_requirement_ids=not_evidenced_ids,
            reason_codes=("match.calibration_unset",),
        )

    scoring_policy = ScoringPolicy.from_target_policy(target_policy)
    if scoring_policy is None:
        raise ValueError("calibrated P2-07 requires target policy scoring section")
    if scoring_policy.calibration_version != calibration_version:
        raise ValueError("target policy scoring calibration version does not match manifest")

    components = _build_components(component_units, scoring_policy, structured_bounds)
    score = _weighted_score(components)
    if score is None:
        return MatchDecisionBundle(
            input_fingerprint=input_fingerprint,
            requirement_qualification_set_sha256=qualification_set_ref_sha256,
            scoring_version=scoring_version,
            calibration_version=calibration_version,
            policy_version=target_policy.policy_version,
            decision=MatchDecision.REVIEW,
            score=ScoreBounds(lower=_ZERO, upper=_HUNDRED),
            deterministic_score=_ZERO,
            components=components,
            unknown_requirement_ids=unknown_ids,
            not_evidenced_requirement_ids=not_evidenced_ids,
            reason_codes=("match.scoring_signal_unavailable",),
        )

    candidate_threshold = scoring_policy.candidate_min_score
    mandatory_threshold = scoring_policy.mandatory_min_support

    if mandatory_support.upper < mandatory_threshold or score.upper < candidate_threshold:
        return MatchDecisionBundle(
            input_fingerprint=input_fingerprint,
            requirement_qualification_set_sha256=qualification_set_ref_sha256,
            scoring_version=scoring_version,
            calibration_version=calibration_version,
            policy_version=target_policy.policy_version,
            decision=MatchDecision.SKIP,
            score=score,
            deterministic_score=score.lower,
            components=components,
            unknown_requirement_ids=unknown_ids,
            not_evidenced_requirement_ids=not_evidenced_ids,
            reason_codes=("match.policy_not_satisfied",),
        )

    if mandatory_support.lower >= mandatory_threshold and score.lower >= candidate_threshold:
        return MatchDecisionBundle(
            input_fingerprint=input_fingerprint,
            requirement_qualification_set_sha256=qualification_set_ref_sha256,
            scoring_version=scoring_version,
            calibration_version=calibration_version,
            policy_version=target_policy.policy_version,
            decision=MatchDecision.APPLICATION_CANDIDATE,
            score=score,
            deterministic_score=score.lower,
            components=components,
            unknown_requirement_ids=unknown_ids,
            not_evidenced_requirement_ids=not_evidenced_ids,
            reason_codes=("match.policy_satisfied",),
        )

    reason = "match.policy_uncertain"
    if any(
        requirement_by_id[item].importance is RequirementImportance.MANDATORY
        for item in not_evidenced_ids
    ):
        reason = "requirements.mandatory_not_evidenced"
    elif any(
        requirement_by_id[item].importance is RequirementImportance.MANDATORY
        for item in unknown_ids
    ):
        reason = "evidence.scope_unresolved"

    return MatchDecisionBundle(
        input_fingerprint=input_fingerprint,
        requirement_qualification_set_sha256=qualification_set_ref_sha256,
        scoring_version=scoring_version,
        calibration_version=calibration_version,
        policy_version=target_policy.policy_version,
        decision=MatchDecision.REVIEW,
        score=score,
        deterministic_score=score.lower,
        components=components,
        unknown_requirement_ids=unknown_ids,
        not_evidenced_requirement_ids=not_evidenced_ids,
        reason_codes=(reason,),
    )
