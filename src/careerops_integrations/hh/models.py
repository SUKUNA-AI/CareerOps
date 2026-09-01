from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from careerops_contracts import CanonicalVacancy, RawVacancyRef


class HHVacancyOperational(BaseModel):
    """HH-only current operational flags kept outside the canonical contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    vacancy_id: str = Field(min_length=1)
    relations: tuple[str, ...] = ()
    has_test: bool = False
    response_url: str | None = None
    response_letter_required: bool = False
    archived: bool = False
    closed_for_applicants: bool = False

    @property
    def already_interacted(self) -> bool:
        """Report whether HH exposes any relationship with the vacancy."""

        return bool(self.relations)


class SyncedHHVacancy(BaseModel):
    """Development sync result containing RAW, canonical, and HH state."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    raw: RawVacancyRef
    canonical: CanonicalVacancy
    operational: HHVacancyOperational
    upstream_payload: dict[str, Any]
