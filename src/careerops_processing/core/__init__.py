"""Pure deterministic Python reference semantics for Processing v2.

This package stays free of PostgreSQL, S3, HTTP and model-serving concerns. It is the
correctness/reference implementation used to define and test deterministic semantics.
Measured production hot paths may later execute in the separate C++20
`careerops-matching-core` service, which must prove parity against this reference layer.
"""

from .filtering import evaluate_filter

__all__ = ["evaluate_filter"]
