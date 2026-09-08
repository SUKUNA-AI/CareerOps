"""Общие exports объектного хранилища

Канонические PostgreSQL v2 schema и table contracts находятся в
``careerops_storage.v2``. Этот пакет экспортирует только S3 primitives
"""

from .s3 import S3JsonStore, S3ObjectRef, S3Settings

__all__ = [
    "S3JsonStore",
    "S3ObjectRef",
    "S3Settings",
]
