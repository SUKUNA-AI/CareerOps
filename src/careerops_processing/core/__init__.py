"""Pure deterministic Processing core boundary.

Implementation intentionally starts in Python. The package must remain free of
PostgreSQL, S3, HTTP and model-serving concerns so measured hot paths can later be
replaced by the native module without changing service orchestration contracts.
"""
