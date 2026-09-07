"""High-recall deterministic vacancy admission filter.

The filter is deliberately asymmetric: it can prove an exclusion, but it never
proves a match. Missing, ambiguous, mixed-role, or partially understood input
therefore remains KEEP and proceeds to later Processing stages.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Iterable

from careerops_processing.contracts.common import SourceLabel, ValueState
from careerops_processing.contracts.filtering import (
    FilterDecision,
    FilterEvidence,
    FilterOutcome,
    FilterPolicy,
    ProvenExclusion,
    RoleFamily,
    SeniorityLevel,
    WorkFormat,
)
from careerops_processing.contracts.normalized import NormalizedResume, NormalizedVacancy
from careerops_processing.contracts.policy import TargetPolicy


_ROLE_PATTERNS: dict[RoleFamily, tuple[re.Pattern[str], ...]] = {
    RoleFamily.ML_ENGINEERING: (
        re.compile(r"\b(?:ml|machine learning)[\s-]*(?:engineer|developer)\b"),
        re.compile(r"\bml[\s-]*(?:инженер|разработчик)\b"),
        re.compile(r"\bинженер(?:\s+по)?\s+машинн(?:ому|ого)\s+обучени[юя]\b"),
        re.compile(r"\bразработчик\s+(?:моделей\s+)?машинного\s+обучения\b"),
    ),
    RoleFamily.DATA_SCIENCE: (
        re.compile(r"\bdata[\s-]*scientist\b"),
        re.compile(r"\bdata science(?:\s+engineer)?\b"),
        re.compile(r"\bдата[\s-]*с[аa]йентист\b"),
    ),
    RoleFamily.AI_LLM: (
        re.compile(r"\b(?:ai|llm|nlp|rag)[\s-]*(?:engineer|developer)\b"),
        re.compile(r"\b(?:ai|llm|nlp|rag)[\s-]*(?:инженер|разработчик)\b"),
        re.compile(r"\b(?:инженер|разработчик)\s+(?:ai|ии|llm|nlp|rag)\b"),
        re.compile(r"\b(?:generative ai|genai)(?:\s+(?:engineer|developer))?\b"),
    ),
    RoleFamily.COMPUTER_VISION: (
        re.compile(r"\bcomputer vision(?:\s+(?:engineer|developer))?\b"),
        re.compile(r"\b(?:cv|vlm)[\s-]*(?:engineer|developer)\b"),
        re.compile(r"\b(?:cv|vlm)[\s-]*(?:инженер|разработчик)\b"),
        re.compile(r"\b(?:инженер|разработчик)\s+компьютерного\s+зрения\b"),
    ),
    RoleFamily.MLOPS: (
        re.compile(r"\bmlops(?:\s+(?:engineer|developer))?\b"),
        re.compile(r"\bml[\s-]*(?:platform|infrastructure)\s+engineer\b"),
        re.compile(r"\b(?:ml|llm)[\s-]*inference\s+engineer\b"),
        re.compile(r"\bmodel serving\s+engineer\b"),
    ),
    RoleFamily.ML_RESEARCH: (
        re.compile(r"\b(?:applied|research)\s+(?:scientist|engineer)\b"),
        re.compile(r"\b(?:ml|ai)[\s-]*researcher\b"),
        re.compile(r"\bисследователь\s+(?:ml|ai|ии|машинного обучения)\b"),
    ),
    RoleFamily.RECOMMENDATION_RANKING: (
        re.compile(
            r"\b(?:recommendation|recommender|ranking|personalization)"
            r"[\s-].*engineer\b"
        ),
        re.compile(r"\b(?:инженер|разработчик)\s+(?:рекомендательных систем|ранжирования)\b"),
    ),
    RoleFamily.DATA_ENGINEERING: (
        re.compile(r"\bdata[\s-]*(?:engineer|engineering)\b"),
        re.compile(r"\b(?:etl|elt|dwh)[\s-]*(?:engineer|developer)\b"),
        re.compile(
            r"\b(?:data platform|data pipeline|data infrastructure|big data|streaming data)"
            r"\s+engineer\b"
        ),
        re.compile(r"\b(?:дата[\s-]*инженер|инженер данных)\b"),
        re.compile(r"\b(?:разработчик|инженер)\s+(?:etl|dwh|хранилищ? данных|витрин данных)\b"),
    ),
    RoleFamily.PYTHON_BACKEND: (
        re.compile(r"\bpython[\s-]*backend(?:\s+(?:developer|engineer))?\b"),
        re.compile(r"\bbackend(?:\s+(?:developer|engineer))?\s+python\b"),
        re.compile(r"\bpython[\s-]*(?:developer|software engineer)\b"),
        re.compile(r"\bpython[\s-]*разработчик\b"),
        re.compile(r"\b(?:бэкенд|бекенд)[\s-]*разработчик\s+python\b"),
    ),
    RoleFamily.CPP: (
        re.compile(r"(?<!\w)c\+\+(?:\s+(?:developer|engineer|programmer))?\b"),
        re.compile(r"\bc/c\+\+(?:\s+(?:developer|engineer))?\b"),
        re.compile(r"\b(?:разработчик|программист|инженер)\s+c\+\+\b"),
    ),
    RoleFamily.DATA_ANALYTICS: (
        re.compile(r"\bdata[\s-]*analyst\b"),
        re.compile(r"\b(?:аналитик данных|аналитик dwh)\b"),
        re.compile(r"\bbi[\s-]*(?:analyst|developer)\b"),
    ),
    RoleFamily.JAVA_BACKEND: (
        re.compile(r"\bjava[\s-]*(?:developer|engineer)\b"),
        re.compile(r"\bbackend(?:\s+(?:developer|engineer))?\s+java\b"),
        re.compile(r"\b(?:разработчик|инженер)\s+java\b"),
    ),
    RoleFamily.FRONTEND: (
        re.compile(r"\bfront[\s-]*end(?:\s+(?:developer|engineer))?\b"),
        re.compile(r"\bfrontend(?:\s+(?:developer|engineer))?\b"),
        re.compile(r"\bфронтенд[\s-]*(?:разработчик|инженер)\b"),
    ),
    RoleFamily.DEVOPS: (
        re.compile(r"\bdevops(?:\s+engineer)?\b"),
        re.compile(r"\bdevops[\s-]*инженер\b"),
    ),
    RoleFamily.QA: (
        re.compile(r"\bqa(?:\s+(?:engineer|automation))?\b"),
        re.compile(r"\btest(?:\s+automation)?\s+engineer\b"),
        re.compile(r"\b(?:qa[\s-]*инженер|тестировщик)\b"),
    ),
    RoleFamily.PRODUCT_MANAGEMENT: (
        re.compile(r"\bproduct\s+manager\b"),
        re.compile(r"\bпродуктов(?:ый|ого)\s+менеджер\b"),
    ),
    RoleFamily.PROJECT_MANAGEMENT: (
        re.compile(r"\bproject\s+manager\b"),
        re.compile(r"\bруководитель\s+проекта\b"),
    ),
    RoleFamily.BUSINESS_ANALYSIS: (
        re.compile(r"\bbusiness\s+analyst\b"),
        re.compile(r"\bбизнес[\s-]*аналитик\b"),
    ),
    RoleFamily.SYSTEM_ANALYSIS: (
        re.compile(r"\bsystem(?:s)?\s+analyst\b"),
        re.compile(r"\bсистемн(?:ый|ого)\s+аналитик\b"),
    ),
    RoleFamily.DBA: (
        re.compile(r"\b(?:database administrator|dba)\b"),
        re.compile(r"\bадминистратор\s+баз(?:ы| данных)\b"),
    ),
}

_SENIORITY_PATTERNS: dict[SeniorityLevel, tuple[re.Pattern[str], ...]] = {
    SeniorityLevel.INTERN: (re.compile(r"\b(?:intern|internship|trainee|стажер|стажёр)\b"),),
    SeniorityLevel.JUNIOR: (re.compile(r"\b(?:junior|jr\.?|младший)\b"),),
    SeniorityLevel.MIDDLE: (re.compile(r"\b(?:middle|mid(?:dle)?|мидл)\b"),),
    SeniorityLevel.SENIOR: (re.compile(r"\b(?:senior|sr\.?|старший)\b"),),
    SeniorityLevel.LEAD: (re.compile(r"\b(?:tech\s+lead|team\s+lead|lead|лид|тимлид)\b"),),
    SeniorityLevel.PRINCIPAL: (re.compile(r"\b(?:principal|staff|ведущий)\b"),),
    SeniorityLevel.HEAD: (re.compile(r"\b(?:head|director|руководитель)\b"),),
}

_MANAGEMENT_PATTERNS = (
    re.compile(r"\bteam\s+lead\b"),
    re.compile(r"\bhead\s+of\b"),
    re.compile(r"\bdirector\b"),
    re.compile(r"\bmanager\b"),
    re.compile(r"\bтимлид\b"),
    re.compile(r"\bруководитель\s+(?:команды|отдела|направления|проекта)\b"),
)

_NEGATION_WORDS = {"no", "not", "without", "не", "нет", "без"}
_RELOCATION_NEGATIVE_PHRASES = (
    "relocation is not required",
    "relocation not required",
    "relocation is not mandatory",
    "relocation not mandatory",
    "no relocation required",
    "no relocation",
    "релокация не требуется",
    "релокация не обязательна",
    "переезд не требуется",
    "переезд не обязателен",
    "без релокации",
    "без переезда",
)


def _normalize(text: str) -> str:
    value = text.casefold().replace("ё", "е")
    value = re.sub(r"[\t\r\n]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _title(vacancy: NormalizedVacancy) -> str | None:
    if vacancy.title.state is not ValueState.KNOWN or vacancy.title.value is None:
        return None
    return _normalize(str(vacancy.title.value))


def _role_families(title: str) -> frozenset[RoleFamily]:
    return frozenset(
        family
        for family, patterns in _ROLE_PATTERNS.items()
        if any(pattern.search(title) for pattern in patterns)
    )


def _seniority_levels(title: str) -> frozenset[SeniorityLevel]:
    return frozenset(
        level
        for level, patterns in _SENIORITY_PATTERNS.items()
        if any(pattern.search(title) for pattern in patterns)
    )


def _label_text(label: SourceLabel) -> str:
    parts = [label.key, label.label or "", label.source_code or ""]
    return _normalize(" ".join(part for part in parts if part))


def _work_format(label: SourceLabel) -> WorkFormat | None:
    text = _label_text(label)
    if any(token in text for token in ("remote", "удален", "дистанц")):
        return WorkFormat.REMOTE
    if any(token in text for token in ("hybrid", "гибрид")):
        return WorkFormat.HYBRID
    if any(token in text for token in ("onsite", "on-site", "office", "офис", "на месте")):
        return WorkFormat.ONSITE
    return None


def _evidence(path: str, value: object) -> FilterEvidence:
    return FilterEvidence(source_path=path, value=str(value))


def _text_evidence(path: str, text: str, locator: str, quote: str) -> FilterEvidence:
    return FilterEvidence(
        source_path=path,
        value=text,
        source_locator=locator,
        quote=quote,
    )


def _exclude(
    *,
    rule_id: str,
    reason_code: str,
    target_policy: TargetPolicy,
    evidence: Iterable[FilterEvidence],
) -> ProvenExclusion:
    return ProvenExclusion(
        rule_id=rule_id,
        reason_code=reason_code,
        policy_version=target_policy.policy_version,
        evidence=tuple(evidence),
    )


def _is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 64) : start]
    words = re.findall(r"[a-zа-я0-9+_-]+", prefix.casefold().replace("ё", "е"))[-4:]
    if any(word in _NEGATION_WORDS for word in words):
        return True
    compact = " ".join(words)
    return any(
        phrase in compact
        for phrase in (
            "не требуется",
            "не требуем",
            "не обязателен",
            "не обязательна",
            "not required",
            "no requirement",
        )
    )


def _unnegated_term(text: str, term: str) -> bool:
    normalized_text = _normalize(text)
    normalized_term = _normalize(term)
    if not normalized_term:
        return False
    start = 0
    while True:
        index = normalized_text.find(normalized_term, start)
        if index < 0:
            return False
        if not _is_negated(normalized_text, index):
            return True
        start = index + len(normalized_term)


def _explicit_relocation_required(fact: str) -> bool:
    text = _normalize(fact)
    if any(phrase in text for phrase in _RELOCATION_NEGATIVE_PHRASES):
        return False

    relocation_tokens = ("relocation", "релокац", "переезд")
    if not any(token in text for token in relocation_tokens):
        return False

    positive_markers = (
        "required",
        "mandatory",
        "обязател",
        "необходим",
        "готовность к переезду",
        "готовность к релокации",
    )
    return any(marker in text for marker in positive_markers)


def _known_work_formats(vacancy: NormalizedVacancy) -> tuple[frozenset[WorkFormat], bool]:
    if not vacancy.work_formats:
        return frozenset(), False
    resolved: set[WorkFormat] = set()
    all_understood = True
    for label in vacancy.work_formats:
        value = _work_format(label)
        if value is None:
            all_understood = False
        else:
            resolved.add(value)
    return frozenset(resolved), all_understood


def evaluate_filter(
    vacancy: NormalizedVacancy,
    target_policy: TargetPolicy,
    resume: NormalizedResume | None = None,
) -> FilterDecision:
    """Return KEEP unless at least one policy-backed exclusion is fully proven."""

    policy = FilterPolicy.from_target_policy(target_policy)
    exclusions: list[ProvenExclusion] = []

    if policy.exclude_unavailable:
        unavailable: list[FilterEvidence] = []
        if vacancy.archived.state is ValueState.KNOWN and vacancy.archived.value is True:
            unavailable.append(_evidence("vacancy.archived", True))
        if (
            vacancy.closed_for_applicants.state is ValueState.KNOWN
            and vacancy.closed_for_applicants.value is True
        ):
            unavailable.append(_evidence("vacancy.closed_for_applicants", True))
        if unavailable:
            exclusions.append(
                _exclude(
                    rule_id="filter.source.unavailable.v1",
                    reason_code="source.vacancy_unavailable",
                    target_policy=target_policy,
                    evidence=unavailable,
                )
            )

    title = _title(vacancy)
    if title is not None:
        families = _role_families(title)
        if families:
            allowed = set(policy.allowed_primary_roles)
            forbidden = set(policy.forbidden_primary_roles)
            proven_disjoint = bool(allowed) and families.isdisjoint(allowed)
            proven_forbidden = bool(forbidden) and families <= forbidden
            if proven_disjoint or proven_forbidden:
                exclusions.append(
                    _exclude(
                        rule_id="filter.role.primary_disjoint.v1",
                        reason_code="filter.primary_role_disjoint",
                        target_policy=target_policy,
                        evidence=(_evidence("vacancy.title", vacancy.title.value),),
                    )
                )

        seniority = _seniority_levels(title)
        forbidden_seniority = set(policy.forbidden_seniority)
        if seniority and forbidden_seniority and seniority <= forbidden_seniority:
            exclusions.append(
                _exclude(
                    rule_id="filter.seniority.forbidden.v1",
                    reason_code="filter.seniority_forbidden",
                    target_policy=target_policy,
                    evidence=(_evidence("vacancy.title", vacancy.title.value),),
                )
            )

        if not policy.management_allowed and any(
            pattern.search(title) for pattern in _MANAGEMENT_PATTERNS
        ):
            exclusions.append(
                _exclude(
                    rule_id="filter.management.forbidden.v1",
                    reason_code="filter.management_forbidden",
                    target_policy=target_policy,
                    evidence=(_evidence("vacancy.title", vacancy.title.value),),
                )
            )

    formats, all_formats_understood = _known_work_formats(vacancy)
    allowed_formats = set(policy.allowed_work_formats)
    if (
        allowed_formats
        and formats
        and all_formats_understood
        and formats.isdisjoint(allowed_formats)
    ):
        exclusions.append(
            _exclude(
                rule_id="filter.work_format.hard_conflict.v1",
                reason_code="policy.hard_work_format_conflict",
                target_policy=target_policy,
                evidence=tuple(
                    _evidence("vacancy.work_formats", _label_text(label))
                    for label in vacancy.work_formats
                ),
            )
        )

    area_restricted = bool(policy.allowed_area_ids or policy.allowed_area_names)
    remote_is_compatible = WorkFormat.REMOTE in formats and (
        not allowed_formats or WorkFormat.REMOTE in allowed_formats
    )
    if area_restricted and all_formats_understood and formats and not remote_is_compatible:
        location = vacancy.location
        if location.state is ValueState.KNOWN and location.value is not None:
            allowed_ids = {value.casefold() for value in policy.allowed_area_ids}
            allowed_names = {_normalize(value) for value in policy.allowed_area_names}
            actual_id = (location.value.area_id or "").casefold()
            actual_name = _normalize(location.value.area_name or "")
            has_area_identity = bool(actual_id or actual_name)
            id_match = bool(actual_id) and actual_id in allowed_ids
            name_match = bool(actual_name) and actual_name in allowed_names
            if has_area_identity and not id_match and not name_match:
                exclusions.append(
                    _exclude(
                        rule_id="filter.location.hard_conflict.v1",
                        reason_code="policy.hard_location_conflict",
                        target_policy=target_policy,
                        evidence=(
                            _evidence(
                                "vacancy.location",
                                location.value.area_id or location.value.area_name,
                            ),
                        ),
                    )
                )

    if not policy.relocation_allowed:
        for fact in vacancy.relocation_facts:
            if _explicit_relocation_required(fact):
                exclusions.append(
                    _exclude(
                        rule_id="filter.relocation.required.v1",
                        reason_code="policy.relocation_required",
                        target_policy=target_policy,
                        evidence=(_evidence("vacancy.relocation_facts", fact),),
                    )
                )
                break

    if (
        resume is not None
        and policy.maximum_experience_gap_years is not None
        and vacancy.experience.state is ValueState.KNOWN
        and vacancy.experience.value is not None
        and vacancy.experience.value.minimum_years is not None
        and resume.total_experience_years.state is ValueState.KNOWN
        and resume.total_experience_years.value is not None
    ):
        required = Decimal(vacancy.experience.value.minimum_years)
        available = Decimal(resume.total_experience_years.value)
        if required > available + policy.maximum_experience_gap_years:
            exclusions.append(
                _exclude(
                    rule_id="filter.experience.gap_exceeded.v1",
                    reason_code="filter.experience_gap_exceeded",
                    target_policy=target_policy,
                    evidence=(
                        _evidence("vacancy.experience.minimum_years", required),
                        _evidence("resume.total_experience_years", available),
                    ),
                )
            )

    if policy.forbidden_context_terms:
        sources: list[tuple[str, str, str | None, str | None]] = []
        if vacancy.title.state is ValueState.KNOWN and vacancy.title.value is not None:
            sources.append(("vacancy.title", str(vacancy.title.value), None, None))
        if vacancy.employer.state is ValueState.KNOWN and vacancy.employer.value is not None:
            if vacancy.employer.value.name:
                sources.append(
                    ("vacancy.employer.name", vacancy.employer.value.name, None, None)
                )
        for block in vacancy.text_blocks:
            sources.append(
                (
                    f"vacancy.text_blocks.{block.block_id}",
                    block.text,
                    block.source_ref.locator,
                    block.source_ref.quote,
                )
            )

        context_evidence: list[FilterEvidence] = []
        for term in policy.forbidden_context_terms:
            for path, text, locator, quote in sources:
                if not _unnegated_term(text, term):
                    continue
                if locator is not None and quote is not None:
                    context_evidence.append(_text_evidence(path, term, locator, quote))
                else:
                    context_evidence.append(_evidence(path, term))
                break
        if context_evidence:
            exclusions.append(
                _exclude(
                    rule_id="filter.context.forbidden.v1",
                    reason_code="policy.forbidden_context",
                    target_policy=target_policy,
                    evidence=context_evidence,
                )
            )

    if not exclusions:
        return FilterDecision(outcome=FilterOutcome.KEEP)
    return FilterDecision(
        outcome=FilterOutcome.EXCLUDE_PROVEN,
        exclusions=tuple(exclusions),
    )
