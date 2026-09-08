"""Канонические PostgreSQL metadata и table contracts схемы v2"""

from .applications import application_guards, applications
from .domain import accounts, employers, profiles, resume_bindings, resumes, sources, vacancies
from .metadata import SCHEMA, metadata
from .processing import application_candidates, match_results, processing_jobs
from .source_control import source_tasks, source_watermarks

__all__ = [
    "SCHEMA",
    "accounts",
    "application_candidates",
    "application_guards",
    "applications",
    "employers",
    "match_results",
    "metadata",
    "processing_jobs",
    "profiles",
    "resume_bindings",
    "resumes",
    "source_tasks",
    "source_watermarks",
    "sources",
    "vacancies",
]
