from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RawVacancyRef(BaseModel):
    """Immutable provenance reference to one collected RAW vacancy object."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    source: str = Field(min_length=1)
    source_entity_id: str = Field(min_length=1)

    raw_uri: str = Field(min_length=1)

    content_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    collected_at: datetime

    @field_validator("collected_at")
    @classmethod
    def normalize_collected_at(cls, value: datetime) -> datetime:
        """Require a timezone-aware collection time and normalize it to UTC."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("collected_at must be timezone-aware")

        return value.astimezone(UTC)


class CanonicalVacancy(BaseModel):
    """Source-neutral normalized vacancy contract used by OLTP persistence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    # Identity
    source: str = Field(min_length=1)
    source_entity_id: str = Field(min_length=1)

    # Main data
    title: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    description: str | None = None

    # Salary
    salary_from: Decimal | None = None
    salary_to: Decimal | None = None
    salary_currency: str | None = None

    # Job properties
    location: str | None = None
    remote: bool | None = None
    employment_type: str | None = None
    experience: str | None = None

    # Source
    source_url: str = Field(min_length=1)

    # Time
    published_at: datetime | None = None
    collected_at: datetime

    # Provenance
    raw_uri: str = Field(min_length=1)
    content_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("published_at", "collected_at")
    @classmethod
    def normalize_datetime(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        """Normalize optional business and collection timestamps to UTC."""

        if value is None:
            return None

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")

        return value.astimezone(UTC)
