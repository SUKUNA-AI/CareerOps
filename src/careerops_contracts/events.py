from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventEnvelope(BaseModel):
    """Common immutable metadata carried by normalized CareerOPS events."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    event_id: UUID
    event_type: str = Field(min_length=1)
    schema_version: int = Field(ge=1)

    producer: str = Field(min_length=1)
    occurred_at: datetime

    run_id: UUID
    entity_key: str = Field(min_length=1)

    raw_uri: str = Field(min_length=1)

    content_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        """Reject ambiguous event timestamps without a timezone."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

        return value
