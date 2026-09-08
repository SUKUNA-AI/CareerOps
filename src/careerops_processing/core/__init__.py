"""Эталонная детерминированная логика Processing v2 на Python

Пакет не зависит от PostgreSQL, S3, HTTP и model serving
Он задаёт эталонную семантику для вычислительных частей Processing
C++20 сервис careerops-matching-core подключается только к измеренным горячим путям
"""

from .evidence import extract_resume_evidence
from .filtering import evaluate_filter
from .requirements import extract_requirements

__all__ = [
    "evaluate_filter",
    "extract_requirements",
    "extract_resume_evidence",
]
