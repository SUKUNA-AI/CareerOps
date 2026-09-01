from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class SourceVacancyRef(BaseModel):
    """Minimal identity returned by an external vacancy discovery adapter."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    source: str = Field(min_length=1)
    source_entity_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)


class SourceAdapter(Protocol):
    """Asynchronous discovery and fetch contract for vacancy sources."""

    @property
    def source_name(self) -> str:
        """Return the stable source identifier implemented by the adapter."""

        ...

    async def discover(self) -> AsyncIterator[SourceVacancyRef]:
        """Yield vacancy references discovered from the source."""

        ...

    async def fetch(self, ref: SourceVacancyRef) -> bytes:
        """Fetch immutable RAW bytes for one discovered vacancy."""

        ...
