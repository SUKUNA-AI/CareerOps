"""Minimal S3 write results; each consumer owns its store behaviour."""

from dataclasses import dataclass


@dataclass(frozen=True)
class JsonWriteRef:
    """URI-only result for JSON writers that do not model hashes or metadata."""

    uri: str
