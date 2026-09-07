"""High-recall deterministic vacancy admission filter.

The filter is deliberately asymmetric: it can prove an exclusion, but it never
proves a match. Missing, ambiguous, mixed-role, or partially understood input
therefore remains KEEP and proceeds to later Processing stages.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal

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


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


_ROLE_PATTERNS: dict[RoleFamily, tuple[re.Pattern[str], ...]] = {
    RoleFamily.ML_ENGINEERING: (
        _rx(r"\b(?:ml|machine learning)[\s-]*(?:engineer|developer)\b"),
        _rx(r"\bml[\s-]*(?:инженер|разработчик)\b"),
        _rx(r"\bинженер(?:\s+по)?\s+машинн(?:ому|ого)\s+обучени[юя]\b"),
        _rx(r"\bразработчик\s+(?:моделей\s+)?машинного\s+обучения\b"),
    ),
    RoleFamily.DATA_SCIENCE: (
        _rx(r"\bdata[\s-]*scientist\b"),
        _rx(r"\bdata science(?:\s+engineer)?\b"),
        _rx(r"\bдата[\s-]*с[аa]йентист\b"),
    ),
    RoleFamily.AI_LLM: (
        _rx(r"\b(?:ai|llm|nlp|rag)[\s-]*(?:engineer|developer)\b"),
        _rx(r"\b(?:ai|llm|nlp|rag)[\s-]*(?:инженер|разработчик)\b"),
        _rx(r"\b(?:инженер|разработчик)\s+(?:ai|ии|llm|nlp|rag)\b"),
        _rx(r"\b(?:generative ai|genai)(?:\s+(?:engineer|developer))?\b"),
    ),
    RoleFamily.COMPUTER_VISION: (
        _rx(r"\bcomputer vision(?:\s+(?:engineer|developer))?\b"),
        _rx(r"\b(?:cv|vlm)[\s-]*(?:engineer|developer)\b"),
        _rx(r"\b(?:cv|vlm)[\s-]*(?:инженер|разработчик)\b"),
        _rx(r"\b(?:инженер|разработчик)\s+компьютерного\s+зрения\b"),
    ),
    RoleFamily.MLOPS: (
        _rx(r"\bmlops(?:\s+(?:engineer|developer))?\b"),
        _rx(r"\bml[\s-]*(?:platform|infrastructure)\s+engineer\b"),
        _rx(r"\b(?:ml|llm)[\s-]*inference\s+engineer\b"),
        _rx(r"\bmodel serving\s+engineer\b"),
    ),
    RoleFamily.ML_RESEARCH: (
        _rx(r"\b(?:applied|research)\s+(?:scientist|engineer)\b"),
        _rx(r"\b(?:ml|ai)[\s-]*researcher\b"),
        _rx(r"\bисследователь\s+(?:ml|ai|ии|машинного обучения)\b"),
    ),
    RoleFamily.RECOMMENDATION_RANKING: (
        _rx(r"\b(?:recommendation|recommender|ranking|personalization)[\s-].*engineer\b"),
        _rx(r"\b(?:инженер|разработчик)\s+(?:рекомендательных систем|ранжирования)\b"),
    ),
    RoleFamily.DATA_ENGINEERING: (
        _rx(r"\bdata[\s-]*(?:engineer|engineering)\b"),
        _rx(r"\b(?:etl|elt|dwh)[\s-]*(?:engineer|developer)\b"),
        _rx(
            r"\b(?:data platform|data pipeline|data infrastructure|big data|streaming data)"
            r"\s+engineer\b"
        ),
        _rx(r"\b(?:дата[\s-]*инженер|инженер данных)\b"),
        _rx(r"\b(?:разработчик|инженер)\s+(?:etl|dwh|хранилищ? данных|витрин данных)\b"),
    ),
    RoleFamily.PYTHON_BACKEND: (
        _rx(r"\bpython[\s-]*backend(?:\s+(?:developer|engineer))?\b"),
        _rx(r"\bbackend(?:\s+(?:developer|engineer))?\s+python\b"),
        _rx(r"\bpython[\s-]*(?:developer|software engineer)\b"),
        _rx(r"\bpython[\s-]*разработчик\b"),
        _rx(r"\b(?:бэкенд|бекенд)[\s-]*разработчик\s+python\b"),
    ),
    RoleFamily.CPP: (
        _rx(r"(?<!\w)c\+\+(?!\w)(?:\s+(?:developer|engineer|programmer))?"),
        _rx(r"(?<!\w)c/c\+\+(?!\w)(?:\s+(?:developer|engineer))?"),
        _rx(r"\b(?:разработчик|программист|инженер)\s+c\+\+(?!\w)"),
    ),
    RoleFamily.DATA_ANALYTICS: (
        _rx(r"\bdata[\s-]*analyst\b"),
        _rx(r"\b(?:аналитик данных|аналитик dwh)\b"),
        _rx(r"\bbi[\s-]*(?:analyst|developer)\b"),
    ),
    RoleFamily.JAVA_BACKEND: (
        _rx(r"\bjava[\s-]*(?:developer|engineer)\b"),
        _rx(r"\bjava\s+backend(?:\s+(?:developer|engineer))?\b"),
        _rx(r"\bbackend(?:\s+(?:developer|engineer))?\s+java\b"),
        _rx(r"\bbackend\s+java(?:\s+(?:developer|engineer))?\b"),
        _rx(r"\b(?:разработчик|инженер)\s+java\b"),
        _rx(r"\bjava[\s-]*(?:бэкенд|бекенд)[\s-]*(?:разработчик|инженер)\b"),
    ),
    RoleFamily.FRONTEND: (
        _rx(r"\bfront[\s-]*end(?:\s+(?:developer|engineer))?\b"),
        _rx(r"\bfrontend(?:\s+(?:developer|engineer))?\b"),
        _rx(r"\bфронтенд[\s-]*(?:разработчик|инженер)\b"),
    ),
    RoleFamily.DEVOPS: (
        _rx(r"\bdevops(?:\s+engineer)?\b"),
        _rx(r"\bdevops[\s-]*инженер\b"),
    ),
    RoleFamily.QA: (
        _rx(r"\bqa(?:\s+(?:engineer|automation))?\b"),
        _rx(r"\btest(?:\s+automation)?\s+engineer\b"),
        _rx(r"\b(?:qa[\s-]*инженер|тестировщик)\b"),
    ),
    RoleFamily.PRODUCT_MANAGEMENT: (
        _rx(r"\bproduct\s+manager\b"),
        _rx(r"\bпродуктов(?:ый|ого)\s+менеджер\b"),
    ),
    RoleFamily.PROJECT_MANAGEMENT: (
        _rx(r"\bproject\s+manager\b"),
        _rx(r"\bруководитель\s+проекта\b"),
    ),
    RoleFamily.BUSINESS_ANALYSIS: (
        _rx(r"\bbusiness\s+analyst\b"),
        _rx(r"\bбизнес[\s-]*аналитик\b"),
    ),
    RoleFamily.SYSTEM_ANALYSIS: (
        _rx(r"\bsystem(?:s)?\s+analyst\b"),
        _rx(r"\bсистемн(?:ый|ого)\s+аналитик\b"),
    ),
    RoleFamily.DBA: (
        _rx(r"\b(?:database administrator|dba)\b"),
        _rx(r"\bадминистратор\s+баз(?:ы| данных)\b"),
    ),
}

_SENIORITY_PATTERNS: dict[SeniorityLevel, tuple[re.Pattern[str], ...]] = {
    SeniorityLevel.INTERN: (_rx(r"\b(?:intern|internship|trainee|стажер|стажёр)\b"),),
    SeniorityLevel.JUNIOR: (_rx(r"\b(?:junior|jr\.?|младший)\b"),),
    SeniorityLevel.MIDDLE: (_rx(r"\b(?:middle|mid(?:dle)?|мидл)\b"),),
    SeniorityLevel.SENIOR: (_rx(r"\b(?:senior|sr\.?|старший)\b"),),
    SeniorityLevel.LEAD: (_rx(r"\b(?:tech\s+lead|team\s+lead|lead|лид|тимлид)\b"),),
    SeniorityLevel.PRINCIPAL: (_rx(r"\b(?:principal|staff|ведущий)\b"),),
    SeniorityLevel.HEAD: (_rx(r"\b(?:head|director|руководитель)\b"),),
}

_MANAGEMENT_PATTERNS = (
    _rx(r"\bteam\s+lead\b"),
    _rx(r"\bhead\s+of\b"),
    _rx(r"\bdirector\b"),
    _rx(r"\bmanager\b"),
    _rx(r"\bтимлид\b"),
    _rx(r"\bруководитель\s+(?:команды|отдела|направления|проекта)\b"),
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
_RELOCATION_POSITIVE_PHRASES = (
    "relocation required",
    "relocation is required",
    "relocation mandatory",
    "relocation is mandatory",
    "релокация обязательна",
    "релокация необходима",
    "требуется релокация",
    "переезд обязателен",
    "переезд необходим",
    "требуется переезд",
    "готовность к переезду",
    "готовность к релокации",
)


def _normalize(text: str) -> str:
    value = text.casefold().replace("ё", "е")
    value = re.sub(r"[\t\r\n]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _known_title(vacancy: NormalizedVacancy) -> str | None:
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
    parts = (label.key, label.label or "", label.source_code or "")
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


def _known_work_formats(vacancy: NormalizedVacancy) -> tuple[frozenset[WorkFormat], bool]:
    if not vacancy.work_formats:
        return frozenset(), False
    resolved: set[WorkFormat] = set()
    for label in vacancy.work_formats:
        value = _work_format(label)
        if value is None:
            return frozenset(resolved), False
        resolved.add(value)
    return frozenset(resolved), True


def _evidence(path: str, value: object) -> FilterEvidence:
    return FilterEvidence(source_path=path, value=str(value))


def _text_evidence(
    path: str,
    value: str,
    locator: str,
    quote: str,
) -> FilterEvidence:
    return FilterEvidence(
        source_path=path,
        value=value,
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
    words = re.findall(r"[a-zа-я0-9+_-]+", _normalize(prefix))[-4:]
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


def _contains_unnegated_term(text: str, term: str) -> bool:
    haystack = _normalize(text)
    needle = _normalize(term)
    if not needle:
        return False
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return False
        if not _is_negated(haystack, index):
            return True
        start = index + len(needle)


def _explicit_relocation_required(fact: str) -> bool:
    text = _normalize(fact)
    if any(phrase in text for phrase in _RELOCATION_NEGATIVE_PHRASES):
        return False
    return any(phrase in text for phrase in _RELOCATION_POSITIVE_PHRASES)


def _location_conflict_proven(
    vacancy: NormalizedVacancy,
    policy: FilterPolicy,
    *,
    formats: frozenset[WorkFormat],
    all_formats_understood: bool,
) -> FilterEvidence | None:
    if not (policy.allowed_area_ids or policy.allowed_area_names):
        return None
    if not all_formats_understood:
        return None
    if WorkFormat.REMOTE in formats:
        return None
    if vacancy.location.state is not ValueState.KNOWN or vacancy.location.value is None:
        return None

    location = vacancy.location.value
    actual_id = location.area_id.casefold() if location.area_id else None
    actual_name = _normalize(location.area_name) if location.area_name else None

    policy_dimensions = 0
    comparable_dimensions = 0
    matched = False

    if policy.allowed_area_ids:
        policy_dimensions += 1
        if actual_id is not None:
            comparable_dimensions += 1
            allowed_ids = {value.casefold() for value in policy.allowed_area_ids}
            matched = matched or actual_id in allowed_ids

    if policy.allowed_area_names:
        policy_dimensions += 1
        if actual_name is not None:
            comparable_dimensions += 1
            allowed_names = {_normalize(value) for value in policy.allowed_area_names}
            matched = matched or actual_name in allowed_names

    if matched:
        return None

    # A hard reject is safe only when every configured location dimension was
    # actually comparable. Otherwise an unseen alternative could still match.
    if policy_dimensions == 0 or comparable_dimensions != policy_dimensions:
        return None

    value = location.area_id or location.area_name
    if value is None:
        return None
    return _evidence("vacancy.location", value)


def _unavailable_exclusion(
    vacancy: NormalizedVacancy,
    target_policy: TargetPolicy,
    policy: FilterPolicy,
) -> ProvenExclusion | None:
    if not policy.exclude_unavailable:
        return None
    evidence: list[FilterEvidence] = []
    if vacancy.archived.state is ValueState.KNOWN and vacancy.archived.value is True:
        evidence.append(_evidence("vacancy.archived", True))
    if (
        vacancy.closed_for_applicants.state is ValueState.KNOWN
        and vacancy.closed_for_applicants.value is True
    ):
        evidence.append(_evidence("vacancy.closed_for_applicants", True))
    if not evidence:
        return None
    return _exclude(
        rule_id="filter.source.unavailable.v1",
        reason_code="source.vacancy_unavailable",
        target_policy=target_policy,
        evidence=evidence,
    )


def _role_exclusion(
    title: str | None,
    vacancy: NormalizedVacancy,
    target_policy: TargetPolicy,
    policy: FilterPolicy,
) -> ProvenExclusion | None:
    if title is None:
        return None
    families = _role_families(title)
    if not families:
        return None

    allowed = set(policy.allowed_primary_roles)
    forbidden = set(policy.forbidden_primary_roles)
    disjoint_from_allowed = bool(allowed) and families.isdisjoint(allowed)
    wholly_forbidden = bool(forbidden) and families <= forbidden
    if not (disjoint_from_allowed or wholly_forbidden):
        return None

    return _exclude(
        rule_id="filter.role.primary_disjoint.v1",
        reason_code="filter.primary_role_disjoint",
        target_policy=target_policy,
        evidence=(_evidence("vacancy.title", vacancy.title.value),),
    )


def _seniority_exclusion(
    title: str | None,
    vacancy: NormalizedVacancy,
    target_policy: TargetPolicy,
    policy: FilterPolicy,
) -> ProvenExclusion | None:
    if title is None or not policy.forbidden_seniority:
        return None
    levels = _seniority_levels(title)
    forbidden = set(policy.forbidden_seniority)
    if not levels or not levels <= forbidden:
        return None
    return _exclude(
        rule_id="filter.seniority.forbidden.v1",
        reason_code="filter.seniority_forbidden",
        target_policy=target_policy,
        evidence=(_evidence("vacancy.title", vacancy.title.value),),
    )


def _management_exclusion(
    title: str | None,
    vacancy: NormalizedVacancy,
    target_policy: TargetPolicy,
    policy: FilterPolicy,
) -> ProvenExclusion | None:
    if title is None or policy.management_allowed:
        return None
    if not any(pattern.search(title) for pattern in _MANAGEMENT_PATTERNS):
        return None
    return _exclude(
        rule_id="filter.management.forbidden.v1",
        reason_code="filter.management_forbidden",
        target_policy=target_policy,
        evidence=(_evidence("vacancy.title", vacancy.title.value),),
    )


def _work_format_exclusion(
    vacancy: NormalizedVacancy,
    target_policy: TargetPolicy,
    policy: FilterPolicy,
    *,
    formats: frozenset[WorkFormat],
    all_formats_understood: bool,
) -> ProvenExclusion | None:
    allowed = set(policy.allowed_work_formats)
    if not allowed or not formats or not all_formats_understood:
        return None
    if not formats.isdisjoint(allowed):
        return None
    return _exclude(
        rule_id="filter.work_format.hard_conflict.v1",
        reason_code="policy.hard_work_format_conflict",
        target_policy=target_policy,
        evidence=tuple(
            _evidence("vacancy.work_formats", _label_text(label))
            for label in vacancy.work_formats
        ),
    )


def _location_exclusion(
    vacancy: NormalizedVacancy,
    target_policy: TargetPolicy,
    policy: FilterPolicy,
    *,
    formats: frozenset[WorkFormat],
    all_formats_understood: bool,
) -> ProvenExclusion | None:
    evidence = _location_conflict_proven(
        vacancy,
        policy,
        formats=formats,
        all_formats_understood=all_formats_understood,
    )
    if evidence is None:
        return None
    return _exclude(
        rule_id="filter.location.hard_conflict.v1",
        reason_code="policy.hard_location_conflict",
        target_policy=target_policy,
        evidence=(evidence,),
    )


def _relocation_exclusion(
    vacancy: NormalizedVacancy,
    target_policy: TargetPolicy,
    policy: FilterPolicy,
) -> ProvenExclusion | None:
    if policy.relocation_allowed:
        return None
    for fact in vacancy.relocation_facts:
        if _explicit_relocation_required(fact):
            return _exclude(
                rule_id="filter.relocation.required.v1",
                reason_code="policy.relocation_required",
                target_policy=target_policy,
                evidence=(_evidence("vacancy.relocation_facts", fact),),
            )
    return None


def _experience_exclusion(
    vacancy: NormalizedVacancy,
    resume: NormalizedResume | None,
    target_policy: TargetPolicy,
    policy: FilterPolicy,
) -> ProvenExclusion | None:
    if resume is None or policy.maximum_experience_gap_years is None:
        return None
    if vacancy.experience.state is not ValueState.KNOWN or vacancy.experience.value is None:
        return None
    if vacancy.experience.value.minimum_years is None:
        return None
    if (
        resume.total_experience_years.state is not ValueState.KNOWN
        or resume.total_experience_years.value is None
    ):
        return None

    required = Decimal(vacancy.experience.value.minimum_years)
    available = Decimal(resume.total_experience_years.value)
    if required <= available + policy.maximum_experience_gap_years:
        return None

    return _exclude(
        rule_id="filter.experience.gap_exceeded.v1",
        reason_code="filter.experience_gap_exceeded",
        target_policy=target_policy,
        evidence=(
            _evidence("vacancy.experience.minimum_years", required),
            _evidence("resume.total_experience_years", available),
        ),
    )


def _forbidden_context_exclusion(
    vacancy: NormalizedVacancy,
    target_policy: TargetPolicy,
    policy: FilterPolicy,
) -> ProvenExclusion | None:
    if not policy.forbidden_context_terms:
        return None

    sources: list[tuple[str, str, str | None, str | None]] = []
    if vacancy.title.state is ValueState.KNOWN and vacancy.title.value is not None:
        sources.append(("vacancy.title", str(vacancy.title.value), None, None))
    if vacancy.employer.state is ValueState.KNOWN and vacancy.employer.value is not None:
        if vacancy.employer.value.name:
            sources.append(("vacancy.employer.name", vacancy.employer.value.name, None, None))
    for block in vacancy.text_blocks:
        sources.append(
            (
                f"vacancy.text_blocks.{block.block_id}",
                block.text,
                block.source_ref.locator,
                block.source_ref.quote,
            )
        )

    evidence: list[FilterEvidence] = []
    for term in policy.forbidden_context_terms:
        for path, text, locator, quote in sources:
            if not _contains_unnegated_term(text, term):
                continue
            if locator is not None and quote is not None:
                evidence.append(_text_evidence(path, term, locator, quote))
            else:
                evidence.append(_evidence(path, term))
            break

    if not evidence:
        return None
    return _exclude(
        rule_id="filter.context.forbidden.v1",
        reason_code="policy.forbidden_context",
        target_policy=target_policy,
        evidence=evidence,
    )


def evaluate_filter(
    vacancy: NormalizedVacancy,
    target_policy: TargetPolicy,
    resume: NormalizedResume | None = None,
) -> FilterDecision:
    """Return KEEP unless at least one policy-backed exclusion is fully proven."""

    policy = FilterPolicy.from_target_policy(target_policy)
    title = _known_title(vacancy)
    formats, all_formats_understood = _known_work_formats(vacancy)

    rules = (
        _unavailable_exclusion(vacancy, target_policy, policy),
        _role_exclusion(title, vacancy, target_policy, policy),
        _seniority_exclusion(title, vacancy, target_policy, policy),
        _management_exclusion(title, vacancy, target_policy, policy),
        _work_format_exclusion(
            vacancy,
            target_policy,
            policy,
            formats=formats,
            all_formats_understood=all_formats_understood,
        ),
        _location_exclusion(
            vacancy,
            target_policy,
            policy,
            formats=formats,
            all_formats_understood=all_formats_understood,
        ),
        _relocation_exclusion(vacancy, target_policy, policy),
        _experience_exclusion(vacancy, resume, target_policy, policy),
        _forbidden_context_exclusion(vacancy, target_policy, policy),
    )
    exclusions = tuple(item for item in rules if item is not None)

    if not exclusions:
        return FilterDecision(outcome=FilterOutcome.KEEP)
    return FilterDecision(
        outcome=FilterOutcome.EXCLUDE_PROVEN,
        exclusions=exclusions,
    )
