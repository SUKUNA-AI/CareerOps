"""Эталонная детерминированная логика Processing v2 на Python

Пакет не зависит от PostgreSQL, S3, HTTP и model serving
Он задаёт correctness semantics, с которыми должен сохранять parity отдельный
C++20 сервис careerops-matching-core для измеренных hot paths
"""

from .filtering import evaluate_filter

__all__ = ["evaluate_filter"]
