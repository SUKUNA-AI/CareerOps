from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


_DOMAIN_PATTERNS: dict[str, re.Pattern[str]] = {
    "ml": re.compile(
        r"(?i)(?:\bmachine\s+learning\b|(?<![a-z0-9])ml(?![a-z0-9])|"
        r"\bml[-\s]?инженер\w*\b|машинн\w*\s+обуч\w*)"
    ),
    "ds": re.compile(
        r"(?i)(?:\bdata\s+scient(?:ist|ists)?\b|\bdata\s+science\b|"
        r"дата[-\s]?сайентист\w*)"
    ),
    "ai": re.compile(
        r"(?i)(?:\bartificial\s+intelligence\b|(?<![a-z0-9])ai(?![a-z0-9])|"
        r"(?<![а-яё])ии(?![а-яё])|искусственн\w*\s+интеллект\w*|"
        r"\bgen(?:erative)?\s*ai\b|\bgenai\b)"
    ),
    "cv": re.compile(
        r"(?i)(?:\bcomputer\s+vision\b|(?<![a-z0-9])cv(?![a-z0-9])|"
        r"компьютерн\w*\s+зрен\w*)"
    ),
    "nlp": re.compile(
        r"(?i)(?<![a-z0-9])nlp(?![a-z0-9])|обработк\w*\s+естественн\w*\s+язык\w*"
    ),
    "llm": re.compile(
        r"(?i)(?<![a-z0-9])llm(?:s)?(?![a-z0-9])|large\s+language\s+model"
    ),
    "vlm": re.compile(
        r"(?i)(?<![a-z0-9])vlm(?:s)?(?![a-z0-9])|vision[-\s]+language"
    ),
    "dl": re.compile(
        r"(?i)(?:\bdeep\s+learning\b|(?<![a-z0-9])dl(?![a-z0-9])|"
        r"глубок\w*\s+обуч\w*)"
    ),
    "mlops": re.compile(
        r"(?i)(?:\bmlops\b|\bml\s+infrastructure\b|ml[-\s]platform)"
    ),
}


# Hard role exclusions. Senior/Middle/Junior/Старший/Ведущий are deliberately allowed.
_BLOCK_PATTERNS: dict[str, re.Pattern[str]] = {
    "leadership": re.compile(
        r"(?i)(?:\btech\s*lead\b|\bteam\s*lead(?:er)?\b|\bteamlead\b|"
        r"(?<![a-z])\blead\b(?![a-z])|тимлид\w*|тим\s*лид\w*|"
        r"\bhead\b|\bdirector\b|\bcto\b|директор\w*|руководител\w*|начальник\w*)"
    ),
    "product_project": re.compile(
        r"(?i)(?:\bproduct\s+(?:manager|owner)\b|\bproject\s+manager\b|"
        r"продакт[-\s]?менеджер\w*|проектн\w*\s+менеджер\w*|"
        r"менеджер\s+продукт\w*|владелец\s+продукт\w*)"
    ),
    "system_business_analyst": re.compile(
        r"(?i)(?:\bsystem\s+analyst\b|\bbusiness\s+analyst\b|"
        r"системн\w*\s+аналитик\w*|бизнес[-\s]?аналитик\w*)"
    ),
    "ios": re.compile(r"(?i)(?<![a-z0-9])ios(?![a-z0-9])|swift\s+developer"),
    "android": re.compile(r"(?i)(?<![a-z0-9])android(?![a-z0-9])|kotlin\s+developer"),
    "mobile": re.compile(
        r"(?i)\bmobile\s+(?:developer|engineer)\b|мобильн\w*\s+разработчик\w*"
    ),
    "frontend": re.compile(r"(?i)\bfront[-\s]?end\b|\bfrontend\b|фронтенд\w*|frontender"),
    "qa": re.compile(
        r"(?i)(?<![a-z0-9])qa(?![a-z0-9])|тестировщик\w*|quality\s+assurance|"
        r"контрол\w*\s+качеств\w*"
    ),
    "1c": re.compile(r"(?i)(?<![a-zа-яё0-9])1[csс](?![a-zа-яё0-9])|\b1с[-\s]?разработчик"),
    "csharp_dotnet": re.compile(r"(?i)(?<![a-z0-9])c#(?![a-z0-9])|(?<![a-z0-9])\.net(?![a-z0-9])"),
    "unity": re.compile(r"(?i)(?<![a-z0-9])unity(?![a-z0-9])"),
    "design": re.compile(
        r"(?i)(?:ux\s*[/&+\-]?\s*ui|ui\s*[/&+\-]?\s*ux|(?<![a-z])ux(?![a-z])|"
        r"(?<![a-z])ui(?![a-z])|designer|дизайнер\w*)"
    ),
    "web_site": re.compile(
        r"(?i)(?:создан\w*\s+сайт\w*|разработк\w*\s+сайт\w*|"
        r"\bweb\s+(?:developer|engineer)\b|веб[-\s]?разработчик\w*|website)"
    ),
    "content_ops": re.compile(
        r"(?i)(?:управлен\w*\s+контент\w*|контент[-\s]?(?:менеджер|специалист)\w*|"
        r"\bcontent\s+(?:manager|specialist)\b)"
    ),
}


# Explicit user exclusions: no drones/UAV, military/defence work, Donetsk/Luhansk/etc.,
# relocation or rotational/shift relocation. We check title during prefilter and full vacancy
# text later because these details often live only in the description.
_EXCLUDED_CONTEXT_PATTERNS: dict[str, re.Pattern[str]] = {
    "drones_uav": re.compile(
        r"(?i)(?:\bбпла\b|беспилот\w*|дрон\w*|\bdrone\w*\b|(?<![a-z0-9])uav(?![a-z0-9])|"
        r"(?<![a-z0-9])uas(?![a-z0-9])|(?<![a-z0-9])fpv(?![a-z0-9]))"
    ),
    "military_defence": re.compile(
        r"(?i)(?:\bсво\b|военн\w*|вооруженн\w*\s+сил\w*|минобор\w*|"
        r"оборонн\w*|оборонно[-\s]?промышлен\w*|гособоронзаказ\w*|\bгоз\b|"
        r"\bmilitary\b|\bdefen[cs]e\b|армейск\w*|\bармия\b)"
    ),
    "war_region": re.compile(
        r"(?i)(?:донецк\w*|\bднр\b|луганск\w*|\bлнр\b|мариупол\w*|"
        r"херсон\w*|запорож\w*)"
    ),
    "relocation": re.compile(
        r"(?i)(?:релокац\w*|\brelocat(?:e|ion|ing)?\b|переезд\w*|"
        r"готовност\w*\s+к\s+переезд\w*|вахт\w*)"
    ),
}

_DEVOPS_PATTERN = re.compile(r"(?i)(?<![a-z0-9])devops(?![a-z0-9])")

_AI_TARGET_ROLE = re.compile(
    r"(?i)(?:engineer|developer|researcher|scientist|architect|"
    r"инженер\w*|разработчик\w*|исследовател\w*|архитектор\w*|сайентист\w*)"
)


@dataclass(frozen=True, slots=True)
class VacancyDecision:
    accepted: bool
    reason: str
    matched_domains: tuple[str, ...] = ()
    blocked_terms: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _excluded_context(text: str) -> tuple[str, ...]:
    return tuple(
        name for name, pattern in _EXCLUDED_CONTEXT_PATTERNS.items() if pattern.search(text)
    )


def _title_decision(title: str) -> VacancyDecision:
    title = title.strip()
    if not title:
        return VacancyDecision(False, "missing_title")

    matched_domains = tuple(
        name for name, pattern in _DOMAIN_PATTERNS.items() if pattern.search(title)
    )
    if not matched_domains:
        return VacancyDecision(False, "title_out_of_scope")

    excluded_context = _excluded_context(title)
    if excluded_context:
        return VacancyDecision(
            False,
            "excluded_context",
            matched_domains=matched_domains,
            blocked_terms=excluded_context,
        )

    blocked_terms = tuple(
        name for name, pattern in _BLOCK_PATTERNS.items() if pattern.search(title)
    )
    if blocked_terms:
        return VacancyDecision(
            False,
            "title_contains_unrelated_role",
            matched_domains=matched_domains,
            blocked_terms=blocked_terms,
        )

    if _DEVOPS_PATTERN.search(title) and "mlops" not in matched_domains:
        return VacancyDecision(
            False,
            "devops_without_mlops",
            matched_domains=matched_domains,
            blocked_terms=("devops",),
        )

    if matched_domains == ("ai",) and not _AI_TARGET_ROLE.search(title):
        return VacancyDecision(
            False,
            "generic_ai_non_engineering_title",
            matched_domains=matched_domains,
        )

    return VacancyDecision(True, "accepted", matched_domains=matched_domains)


def prefilter_ml_search_item(search_item: dict[str, Any]) -> VacancyDecision:
    """Cheap title-only gate for HH search results before full vacancy fetch."""
    return _title_decision(str(search_item.get("name") or ""))


def _area_id(vacancy: dict[str, Any]) -> int | None:
    area = vacancy.get("area") or {}
    raw = area.get("id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _full_context(vacancy: dict[str, Any]) -> str:
    area = vacancy.get("area") or {}
    address = vacancy.get("address") or {}
    employer = vacancy.get("employer") or {}
    parts = [
        str(vacancy.get("name") or ""),
        str(vacancy.get("description") or ""),
        str(area.get("name") or ""),
        str(address.get("raw") or ""),
        str(employer.get("name") or ""),
    ]
    return "\n".join(parts)


def validate_ml_vacancy(
    vacancy: dict[str, Any],
    *,
    required_area_id: int = 1,
) -> VacancyDecision:
    """Precision gate for the CareerOPS ML/DS/AI profile.

    HH experience is intentionally ignored: every experience level is eligible.
    Tests are also eligible and are executed through hh-applicant-tool's native
    vacancy-test mechanism by the application layer.
    """

    title_decision = _title_decision(str(vacancy.get("name") or ""))
    if not title_decision.accepted:
        return title_decision

    excluded_context = _excluded_context(_full_context(vacancy))
    if excluded_context:
        return VacancyDecision(
            False,
            "excluded_context",
            matched_domains=title_decision.matched_domains,
            blocked_terms=excluded_context,
        )

    relations = tuple(str(value) for value in (vacancy.get("relations") or []))
    if relations:
        return VacancyDecision(
            False,
            "already_has_hh_relation",
            matched_domains=title_decision.matched_domains,
        )

    if vacancy.get("archived"):
        return VacancyDecision(False, "archived", matched_domains=title_decision.matched_domains)

    if vacancy.get("closed_for_applicants"):
        return VacancyDecision(
            False,
            "closed_for_applicants",
            matched_domains=title_decision.matched_domains,
        )

    if vacancy.get("response_url"):
        return VacancyDecision(
            False,
            "external_response_url",
            matched_domains=title_decision.matched_domains,
        )

    area_id = _area_id(vacancy)
    if area_id is not None and area_id != required_area_id:
        return VacancyDecision(
            False,
            f"area_{area_id}_not_allowed",
            matched_domains=title_decision.matched_domains,
        )

    return title_decision
