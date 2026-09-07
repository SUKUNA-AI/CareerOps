# Processing v2 native boundary

This directory reserves the future **C++20 + pybind11** computational boundary.

There is intentionally **no native implementation in P2-01**. Python is the reference
implementation until profiling shows a worthwhile pure-compute hot path.

The future native layer may own batched deterministic work such as:

- compiled dictionaries, aliases and pattern matching;
- Processing-level role/technology feature canonicalization;
- experience interval compatibility;
- requirement-group evaluation;
- evidence aggregation and conflict handling;
- deterministic score/policy fan-out.

The native layer must remain a pure computation library. It must never own:

- PostgreSQL connections or transactions;
- S3 reads/writes;
- HTTP or Jina serving;
- Spark/JVM data processing;
- job claims, leases, retries or artifact publication.

Inputs and outputs must be explicit typed values with deterministic ordering and no
Python ORM/client objects. A future C++ implementation must prove parity against the
frozen Python reference implementation before replacing any production hot path.
