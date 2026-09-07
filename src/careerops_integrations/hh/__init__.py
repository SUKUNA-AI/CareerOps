"""Shared read-only HH transport primitives used by the v2 source adapter.

Filtering, materialization, scheduling and application orchestration do not live in
this compatibility package. The vendored hh-applicant-tool remains the pinned
upstream transport implementation and is intentionally untouched.
"""

from .driver import HHApplicantToolCLI, HHDriverError, ParamValue

__all__ = [
    "HHApplicantToolCLI",
    "HHDriverError",
    "ParamValue",
]
