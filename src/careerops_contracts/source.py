from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class SourceVacancyRef(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    source: str = Field(min_length=1)
    source_entity_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)


class SourceAdapter(Protocol):
    @property
    def source_name(self) -> str:
        ...

    async def discover(self) -> AsyncIterator[SourceVacancyRef]:
        ...

    async def fetch(self, ref: SourceVacancyRef) -> bytes:
        ...