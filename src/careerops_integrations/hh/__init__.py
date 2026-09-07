"""Shared HH transport primitives used by the v2 source adapter.

Discovery, filtering, materialization, scheduling and application orchestration do
not live in this compatibility package. The vendored hh-applicant-tool remains the
pinned upstream transport implementation and is intentionally untouched.
"""

from .driver import HHApplicantToolCLI, HHDriverError, HHVacancySearchPage, ParamValue
from .runtime import HHExternalWriteGuard, RuntimeMode

__all__ = [
    "HHApplicantToolCLI",
    "HHDriverError",
    "HHExternalWriteGuard",
    "HHVacancySearchPage",
    "ParamValue",
    "RuntimeMode",
]
