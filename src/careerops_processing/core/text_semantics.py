"""Детерминированные текстовые helpers для P2-04"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

from careerops_processing.contracts.semantics import SemanticPolarity, SemanticSubject

_WS = re.compile(r"\s+")
_BULLET = re.compile(r"^\s*(?:[-*•▪◦‣–—]|\d+[.)])\s*")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ])")
_ALT_WORD = re.compile(r"\s+(?:или|or)\s+", re.IGNORECASE)
_COMPACT_TECH = re.compile(r"[A-Za-zА-Яа-яЁё0-9+#.]", re.UNICODE)

_NEGATION_PATTERNS = (
    re.compile(
        r"\bне\s+(?:работал|работала|работали|использовал|"
        r"использовала|использовали)\s+(?:с|со)\b",
        re.I,
    ),
    re.compile(r"\bнет\s+опыта\b", re.I),
    re.compile(r"\bбез\s+опыта\b", re.I),
    re.compile(r"\bnot\s+worked\s+with\b", re.I),
    re.compile(r"\bno\s+experience\s+with\b", re.I),
    re.compile(r"\bwithout\s+experience\b", re.I),
    re.compile(r"\bnot\s+required\b", re.I),
    re.compile(r"\bне\s+требуется\b", re.I),
)

_MIN_YEARS_PATTERNS = (
    re.compile(
        r"(?:от|не\s+менее|минимум)\s*(?P<years>\d+(?:[.,]\d+)?)\s*"
        r"(?:года|год|лет)\b",
        re.I,
    ),
    re.compile(
        r"(?P<years>\d+(?:[.,]\d+)?)\s*\+?\s*(?:years?|yrs?|y)\b",
        re.I,
    ),
)

_EXPERIENCE_FRAGMENT = re.compile(
    r"(?:\b(?:опыт|experience)\b[^,;]{0,28})?"
    r"(?:от|не\s+менее|минимум)?\s*\d+(?:[.,]\d+)?\s*\+?\s*"
    r"(?:года|год|лет|years?|yrs?|y)\b",
    re.I,
)

_PREFERRED_MARKERS = (
    "будет плюсом",
    "будет преимуществом",
    "как плюс",
    "желательно",
    "приветствуется",
    "nice to have",
    "would be a plus",
    "is a plus",
    "preferred",
    "desirable",
    "advantage",
)

_OPTIONAL_MARKERS = (
    "необязательно",
    "опционально",
    "optional",
)

_REQUIRED_MARKERS = (
    "обязательно",
    "необходимо",
    "требуется",
    "требования",
    "must have",
    "must",
    "required",
    "requirement",
    "не менее",
)

_TEAM_MARKERS = (
    re.compile(r"\bкоманд[аые]\b", re.I),
    re.compile(r"\bмы\b", re.I),
    re.compile(r"\bteam\b", re.I),
    re.compile(r"\bwe\b", re.I),
)

_ACTIVITY_PATTERNS = (
    re.compile(
        r"\b(?:разработал|разработала|разрабатывал|"
        r"разрабатывала|реализовал|реализовала|внедрил|"
        r"внедрила|настроил|настроила|поддерживал|"
        r"поддерживала|оптимизировал|оптимизировала|"
        r"проектировал|проектировала|использовал|использовала|писал|"
        r"писала|создал|создала|управлял|управляла|анализировал|"
        r"анализировала|работал|работала)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:developed|implemented|built|operated|configured|maintained|optimized|designed|"
        r"used|wrote|created|managed|analyzed|worked)\b",
        re.I,
    ),
)

_SUBJECT_PREFIXES = (
    re.compile(
        r"^(?:опыт\s+(?:работы|разработки)?\s*(?:с|со|в)?\s*)",
        re.I,
    ),
    re.compile(r"^(?:experience\s+(?:working\s+)?with\s+)", re.I),
    re.compile(r"^(?:знание|знания|knowledge\s+of)\s+", re.I),
    re.compile(r"^(?:владение|proficiency\s+in)\s+", re.I),
    re.compile(r"^(?:не\s+работал(?:а|и)?\s+(?:с|со)\s+)", re.I),
    re.compile(r"^(?:no\s+experience\s+with\s+)", re.I),
)


class RequirementSignal:
    """Легкий internal value object для modality detection"""

    __slots__ = ("importance", "modality")

    def __init__(self, importance: str, modality: str) -> None:
        self.importance = importance
        self.modality = modality


def normalize_space(value: str) -> str:
    return _WS.sub(" ", value.strip())


def normalize_subject(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = normalize_space(value).casefold()
    return value.strip(" ,;:.()[]{}")


def stable_semantic_id(prefix: str, payload: object) -> str:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def detect_polarity(value: str) -> SemanticPolarity:
    if any(pattern.search(value) for pattern in _NEGATION_PATTERNS):
        return SemanticPolarity.NEGATIVE
    return SemanticPolarity.POSITIVE


def detect_requirement_signal(
    value: str,
    *,
    section_hint: str | None = None,
    heading: str | None = None,
) -> RequirementSignal:
    combined = " ".join(part for part in (heading, section_hint, value) if part).casefold()
    if any(marker in combined for marker in _OPTIONAL_MARKERS):
        return RequirementSignal("optional", "optional")
    if any(marker in combined for marker in _PREFERRED_MARKERS):
        return RequirementSignal("preferred", "preferred")
    if any(marker in combined for marker in _REQUIRED_MARKERS):
        return RequirementSignal("mandatory", "required")
    return RequirementSignal("unknown", "unknown")


def extract_minimum_years(value: str) -> Decimal | None:
    for pattern in _MIN_YEARS_PATTERNS:
        match = pattern.search(value)
        if match is None:
            continue
        try:
            return Decimal(match.group("years").replace(",", "."))
        except InvalidOperation:
            return None
    return None


def strip_experience_fragment(value: str) -> str:
    return normalize_space(_EXPERIENCE_FRAGMENT.sub(" ", value))


def strip_requirement_markers(value: str) -> str:
    result = normalize_space(_BULLET.sub("", value))
    lowered = result.casefold()
    for marker in (*_PREFERRED_MARKERS, *_OPTIONAL_MARKERS, *_REQUIRED_MARKERS):
        marker_index = lowered.find(marker)
        if marker_index == 0:
            result = normalize_space(result[len(marker) :].lstrip(" :-—–"))
            lowered = result.casefold()
        elif marker_index > 0 and marker_index >= len(result) - len(marker) - 4:
            result = normalize_space(result[:marker_index].rstrip(" :-—–,"))
            lowered = result.casefold()
    result = strip_experience_fragment(result)
    for pattern in _SUBJECT_PREFIXES:
        result = normalize_space(pattern.sub("", result))
    return result.strip(" ,;:.()[]{}")


def split_statements(
    value: str,
    *,
    split_compact_lists: bool = False,
) -> tuple[str, ...]:
    raw_lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result: list[str] = []
    for raw_line in raw_lines:
        line = normalize_space(_BULLET.sub("", raw_line))
        if not line:
            continue
        semicolon_parts = [normalize_space(part) for part in re.split(r"\s*;\s*", line)]
        for part in semicolon_parts:
            if not part:
                continue
            compact_parts = _split_compact_list(part) if split_compact_lists else (part,)
            for compact_part in compact_parts:
                if len(compact_part) > 120:
                    sentences = [
                        normalize_space(item)
                        for item in _SENTENCE_BOUNDARY.split(compact_part)
                    ]
                    result.extend(item for item in sentences if item)
                else:
                    result.append(compact_part)
    return tuple(result)


def _split_compact_list(value: str) -> tuple[str, ...]:
    if value.count(",") < 2:
        return (value,)
    parts = tuple(normalize_space(part) for part in value.split(",") if normalize_space(part))
    if len(parts) < 3 or any(len(part) > 80 for part in parts):
        return (value,)
    if sum(bool(_COMPACT_TECH.search(part)) for part in parts) < 3:
        return (value,)
    return parts


def split_condition(value: str) -> tuple[str, str] | None:
    normalized = normalize_space(value)
    match = re.match(r"^(?:если|if|when)\s+(.+?)[,:-]\s*(.+)$", normalized, re.I)
    if match is None:
        return None
    condition = normalize_space(match.group(1))
    body = normalize_space(match.group(2))
    if not condition or not body:
        return None
    return condition, body


def split_alternatives(value: str) -> tuple[str, ...]:
    normalized = normalize_space(value)
    parts = tuple(normalize_space(part) for part in _ALT_WORD.split(normalized))
    if 2 <= len(parts) <= 4 and all(0 < len(part) <= 80 for part in parts):
        return parts
    slash_match = re.fullmatch(
        r"([A-Za-zА-Яа-яЁё0-9+#.\-]+)\s*/\s*([A-Za-zА-Яа-яЁё0-9+#.\-]+)",
        normalized,
    )
    if slash_match is not None:
        return (slash_match.group(1), slash_match.group(2))
    return (normalized,)


def subjects_from_statement(
    statement: str,
    *,
    known_subjects: Iterable[str] = (),
) -> tuple[SemanticSubject, ...]:
    normalized_statement = normalize_subject(statement)
    found: dict[str, SemanticSubject] = {}
    for known in known_subjects:
        normalized_known = normalize_subject(known)
        if not normalized_known:
            continue
        if normalized_known in normalized_statement:
            found[normalized_known] = SemanticSubject(
                text=normalize_space(known),
                normalized=normalized_known,
            )
    if found:
        return tuple(found[key] for key in sorted(found))

    candidate = strip_requirement_markers(statement)
    if candidate and len(candidate) <= 80:
        normalized = normalize_subject(candidate)
        if normalized:
            return (SemanticSubject(text=candidate, normalized=normalized),)
    return ()


def detect_team_scope(value: str) -> bool:
    return any(pattern.search(value) for pattern in _TEAM_MARKERS)


def detect_activity(value: str) -> str | None:
    for pattern in _ACTIVITY_PATTERNS:
        match = pattern.search(value)
        if match is not None:
            return match.group(0).casefold()
    return None
