"""Infrastructure adapters for the standalone Processing v2 service."""

from .postgres_jobs import PostgresProcessingJobStore

__all__ = ["PostgresProcessingJobStore"]
