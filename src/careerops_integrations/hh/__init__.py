"""Общие read-only примитивы HH transport для source adapter v2

Фильтрация, нормализация, orchestration и application side effects не принадлежат
этому пакету. Vendored hh-applicant-tool остаётся pinned transport implementation
и не изменяется в рамках обычного CareerOPS refactoring
"""

from .driver import HHApplicantToolCLI, HHDriverError, ParamValue

__all__ = [
    "HHApplicantToolCLI",
    "HHDriverError",
    "ParamValue",
]
