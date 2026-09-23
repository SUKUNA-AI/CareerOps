from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .domain import ApplicationStateView
from .owner import ApplicationOwner


@dataclass(frozen=True, slots=True)
class GetApplicationState:
    application_id: UUID


class GetApplicationStateHandler:
    def __init__(self, owner: ApplicationOwner) -> None:
        self._owner = owner

    async def __call__(self, query: GetApplicationState) -> ApplicationStateView | None:
        return await self._owner.get_state(query.application_id)
