"""Deterministic Python core retained for P2-03/P2-04 only.

P2-06 qualification and P2-07 scoring/decision are owned exclusively by the
C++20 careerops-matching-core service and are reached through the gRPC boundary.
"""

from .evidence import extract_resume_evidence
from .filtering import evaluate_filter
from .requirements import extract_requirements

__all__ = [
    "evaluate_filter",
    "extract_requirements",
    "extract_resume_evidence",
]
