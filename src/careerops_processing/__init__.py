"""CareerOPS Processing v2 — граница matching intelligence

Spark владеет source normalization
Этот пакет потребляет versioned normalized vacancy/resume inputs и владеет filtering,
evidence matching и deterministic policy
"""

from .contracts import ProcessingInputManifest

__all__ = ["ProcessingInputManifest"]
