"""Shared object-storage exports.

Canonical PostgreSQL schema and table contracts live under ``careerops_storage.v2``.
Legacy PostgreSQL runtime writers were removed during the Processing v2 cutover.
"""

from .s3 import S3JsonStore, S3ObjectRef, S3Settings

__all__ = [
    "S3JsonStore",
    "S3ObjectRef",
    "S3Settings",
]
