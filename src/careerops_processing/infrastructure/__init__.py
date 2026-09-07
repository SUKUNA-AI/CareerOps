"""Infrastructure adapters для отдельного Processing v2 сервиса"""

from .policy_repository import FileTargetPolicyRepository, TargetPolicyRepository
from .postgres_jobs import PostgresProcessingJobStore

__all__ = [
    "FileTargetPolicyRepository",
    "PostgresProcessingJobStore",
    "TargetPolicyRepository",
]
