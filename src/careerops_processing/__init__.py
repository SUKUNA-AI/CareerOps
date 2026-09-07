"""CareerOPS Processing v2: matching intelligence boundary.

Spark/Scala owns source normalization. This package consumes versioned normalized
vacancy/resume inputs and owns filtering, evidence matching and deterministic policy.
"""

from .contracts import ProcessingInputManifest

__all__ = ["ProcessingInputManifest"]
