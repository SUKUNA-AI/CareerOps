from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any

from careerops_contracts import CanonicalVacancy, RawVacancyRef

from .models import HHVacancyOperational


class _TextExtractor(HTMLParser):
    """Collect visible text from the limited HTML in HH descriptions."""

    def __init__(self) -> None:
        """Initialize the standard parser and text accumulator."""

        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """Collect one non-empty visible HTML text fragment."""

        value = data.strip()
        if value:
            self.parts.append(value)

    def text(self) -> str:
        """Join collected fragments into normalized plain text."""

        return "\n".join(self.parts)


def _html_to_text(value: str | None) -> str | None:
    """Convert optional HH description HTML into plain text."""

    if not value:
        return None
    parser = _TextExtractor()
    parser.feed(value)
    return parser.text().strip() or None


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an optional timezone-aware HH timestamp."""

    if not value:
        return None
    normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)
    result = datetime.fromisoformat(normalized)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"HH datetime is timezone-naive: {value!r}")
    return result


def _decimal(value: Any) -> Decimal | None:
    """Convert an optional source number to a lossless Decimal."""

    return None if value is None else Decimal(str(value))


def _is_remote(payload: dict[str, Any]) -> bool:
    """Detect remote work across current HH schedule and work-format fields."""

    if str((payload.get("schedule") or {}).get("id", "")).lower() == "remote":
        return True
    return any(
        str(item.get("id", "")).upper() == "REMOTE"
        for item in (payload.get("work_format") or [])
    )


def extract_operational(payload: dict[str, Any]) -> HHVacancyOperational:
    """Extract HH-only current flags from a full vacancy payload."""

    return HHVacancyOperational(
        vacancy_id=str(payload["id"]),
        relations=tuple(str(x) for x in (payload.get("relations") or [])),
        has_test=bool(payload.get("has_test")),
        response_url=payload.get("response_url"),
        response_letter_required=bool(payload.get("response_letter_required")),
        archived=bool(payload.get("archived")),
        closed_for_applicants=bool(payload.get("closed_for_applicants")),
    )


def map_hh_vacancy(payload: dict[str, Any], *, raw: RawVacancyRef) -> CanonicalVacancy:
    """Map a full HH payload and immutable RAW provenance to canonical form."""

    employer = payload.get("employer") or {}
    company_name = employer.get("name")
    if not company_name:
        raise ValueError(f"HH vacancy {payload.get('id')} has no employer.name")

    salary = payload.get("salary") or payload.get("salary_range") or {}
    employment = payload.get("employment") or {}
    employment_form = payload.get("employment_form") or {}
    employment_type = employment.get("id") or employment_form.get("id")
    area = payload.get("area") or {}

    return CanonicalVacancy(
        source="hh",
        source_entity_id=str(payload["id"]),
        title=str(payload["name"]),
        company_name=str(company_name),
        description=_html_to_text(payload.get("description")),
        salary_from=_decimal(salary.get("from")),
        salary_to=_decimal(salary.get("to")),
        salary_currency=salary.get("currency"),
        location=area.get("name"),
        remote=_is_remote(payload),
        employment_type=str(employment_type) if employment_type else None,
        experience=(payload.get("experience") or {}).get("id"),
        source_url=str(payload["alternate_url"]),
        published_at=_parse_datetime(payload.get("published_at")),
        collected_at=raw.collected_at,
        raw_uri=raw.raw_uri,
        content_hash=raw.content_hash,
    )
