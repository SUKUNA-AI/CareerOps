from __future__ import annotations

from dataclasses import dataclass

from .owner import ApplicationOwner


@dataclass(frozen=True, slots=True)
class ExecuteNextApplication:
    pass


class ExecuteNextApplicationHandler:
    def __init__(self, owner: ApplicationOwner) -> None:
        self._owner = owner

    async def __call__(self, command: ExecuteNextApplication) -> bool:
        del command
        return await self._owner.execute_next()
