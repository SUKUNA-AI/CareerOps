"""Инфраструктурные адаптеры отдельного сервиса Processing v2"""

from .artifact_loader import ProcessingArtifactLoader
from .artifact_publisher import ProcessingArtifactPublisher
from .artifacts import (
    ProcessingArtifactIntegrityError,
    ProcessingArtifactStore,
    ProcessingArtifactStoreSettings,
)
from .input_loader import S3ProcessingInputLoader
from .policy_repository import FileTargetPolicyRepository, TargetPolicyRepository
from .postgres_jobs import PostgresProcessingJobStore
from .postgres_match_publication import PostgresMatchPublicationStore
from .postgres_semantic_cache import PostgresSemanticArtifactRegistry
from .reranker_http import HttpJinaRerankerClient

__all__ = [
    "FileTargetPolicyRepository",
    "HttpJinaRerankerClient",
    "PostgresMatchPublicationStore",
    "PostgresProcessingJobStore",
    "PostgresSemanticArtifactRegistry",
    "ProcessingArtifactIntegrityError",
    "ProcessingArtifactLoader",
    "ProcessingArtifactPublisher",
    "ProcessingArtifactStore",
    "ProcessingArtifactStoreSettings",
    "S3ProcessingInputLoader",
    "TargetPolicyRepository",
]
