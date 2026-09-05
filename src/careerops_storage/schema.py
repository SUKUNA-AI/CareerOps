"""Legacy import boundary. New code must import careerops_storage.v2 instead."""

from .legacy.schema import (
    CAREEROPS_SCHEMA,
    application_claims,
    applications,
    batch_runs,
    evaluation_work_items,
    metadata,
    observation_runs,
    observe_query_cursors,
    resumes,
    source_profiles,
    vacancies,
    vacancy_decisions,
    vacancy_observations,
)

__all__ = [
    "CAREEROPS_SCHEMA",
    "application_claims",
    "applications",
    "batch_runs",
    "evaluation_work_items",
    "metadata",
    "observation_runs",
    "observe_query_cursors",
    "resumes",
    "source_profiles",
    "vacancies",
    "vacancy_decisions",
    "vacancy_observations",
]
