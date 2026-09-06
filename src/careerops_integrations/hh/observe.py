"""Retired legacy HH OBSERVE compatibility surface.

The broad v1 OBSERVE runner was replaced by ``careerops_adapter.hh`` persistent
source tasks and immutable RAW ingestion. The tiny cursor types remain temporarily
because legacy APPLY/storage modules still import them; no OBSERVE execution path
is retained here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ObserveQueryCursorReservation:
    """Legacy cursor value kept only until v1 storage cleanup."""

    source_profile: str
    account_key: str
    catalog_signature: str
    catalog_size: int
    window_start: int
    window_size: int
    next_query_offset: int

    @property
    def wrapped(self) -> bool:
        return self.window_start + self.window_size >= self.catalog_size


class ObserveQueryCursorStore(Protocol):
    """Legacy protocol retained so the APPLY CLI can still import cleanly."""

    async def reserve(self, **kwargs: Any) -> ObserveQueryCursorReservation:
        ...


class HHObserveRunner:
    """Fail closed if any retired scheduler still tries to launch v1 OBSERVE."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(
            "legacy HH OBSERVE is retired; use `python -m careerops_adapter.hh seed/work`"
        )

    async def run(self, *, run_id: UUID | None = None) -> Any:  # pragma: no cover
        del run_id
        raise RuntimeError("legacy HH OBSERVE is retired")
