"""Lightweight transaction recording for fake-backed PostgreSQL consumers."""

from contextlib import AbstractAsyncContextManager
from types import TracebackType


class TransactionRecorder(AbstractAsyncContextManager[None]):
    """Record transaction boundaries without modelling database state."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def __aenter__(self) -> None:
        self.events.append("begin")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.events.append("rollback" if exc_type else "commit")
        return False
