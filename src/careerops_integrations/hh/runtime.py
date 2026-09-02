"""Fail-closed runtime modes and the centralized HH external-write capability."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class RuntimeMode(StrEnum):
    """The only two CareerOPS HH runtime modes."""

    OBSERVE = "observe"
    APPLY = "apply"

    @classmethod
    def parse(cls, value: str | RuntimeMode | None) -> RuntimeMode:
        """Parse a mode without ever guessing an unknown value."""

        if isinstance(value, cls):
            return value
        normalized = cls.OBSERVE.value if value is None else value.strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            allowed = ", ".join(mode.value for mode in cls)
            raise ValueError(
                f"invalid HH runtime mode {value!r}; expected one of: {allowed}"
            ) from exc


class HHExternalWriteForbidden(RuntimeError):
    """Signal that an employer-facing HH write is not authorized."""


def parse_env_bool(value: str | None, *, default: bool = False) -> bool:
    """Parse an explicit environment boolean and reject ambiguous values."""

    if value is None or value.strip() == "":
        return default
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"invalid boolean value {value!r}; expected 'true' or 'false'")


@dataclass(frozen=True, slots=True)
class HHExternalWriteGuard:
    """Central capability required by every employer-facing HH write path."""

    runtime_mode: RuntimeMode = RuntimeMode.OBSERVE
    allow_external_writes: bool = False

    @classmethod
    def from_env(cls, runtime_mode: RuntimeMode) -> HHExternalWriteGuard:
        """Build the guard from the canonical mode and explicit opt-in flag."""

        return cls(
            runtime_mode=runtime_mode,
            allow_external_writes=parse_env_bool(
                os.getenv("CAREEROPS_HH_ALLOW_EXTERNAL_WRITES"),
                default=False,
            ),
        )

    @property
    def external_writes_allowed(self) -> bool:
        """Return whether both independent write conditions are satisfied."""

        return (
            self.runtime_mode is RuntimeMode.APPLY
            and self.allow_external_writes
        )

    def require(self, capability: str) -> None:
        """Fail closed unless APPLY and the external opt-in are both active."""

        if self.external_writes_allowed:
            return
        raise HHExternalWriteForbidden(
            "HH external write denied for "
            f"{capability!r}: mode={self.runtime_mode.value!r}, "
            "CAREEROPS_HH_ALLOW_EXTERNAL_WRITES must be exactly 'true' in APPLY"
        )

    def validate_write_capable_startup(self) -> None:
        """Reject APPLY before any write-capable execution is constructed."""

        self.require("write-capable runtime startup")
