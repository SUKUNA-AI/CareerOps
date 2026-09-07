# Processing v2 foundation (P2-01)

Status: implementation foundation for `ar/processing-v2`.

## Scope

P2-01 creates the permanent contract and deployment boundaries for Processing v2.
It does **not** implement Spark, queue semantics, filtering, Jina inference, matching math
or APPLY. Those layers build on the boundaries introduced here.

## Physical service split

```text
EDGE
  careerops-reranker
    Jina reranker runtime / GTX 1650

CORE
  careerops-processing
    Python orchestration service

  careerops-matching-core
    C++20 deterministic compute service / Ryzen 7 5800X
```

`careerops-processing` and `careerops-matching-core` are separate Docker containers.
The monorepo is a source-code organization choice, not a deployment monolith.

## Spark ownership boundary

```text
Spark / Scala
  RAW -> parse / normalize / dedup / DQ
  -> immutable normalized entity bundles
  -> PostgreSQL current refs

                 contract boundary

careerops-processing
  exact vacancy version + exact resume version + binding/target snapshot
  -> matching intelligence in later P2 stages
```

Processing has no dependency on Spark internals. A producer is compatible when it can
publish data conforming to `NormalizedVacancy`, `NormalizedResume` and `NormalizedRef`.

## Stable identity and versioning

The contract deliberately distinguishes:

- `raw_sha256`: exact source observation;
- `normalized_sha256`: exact serialized normalized bundle;
- `semantic_content_hash`: matching-relevant content identity.

`NormalizedRef` pins source identity, exact RAW provenance, normalized object URI/hash,
semantic hash, normalized schema/normalizer/dictionary versions, materialization key and
processing readiness.

A resume ref is account-scoped. A vacancy ref must not carry account scope.

## Missing and unknown values

Source-backed scalar values use explicit states:

- `known`;
- `not_provided`;
- `explicit_none`;
- `unknown_parse`.

This prevents source absence or parse uncertainty from becoming `false`, `0` or an empty
string and later turning into a false-negative matching signal.

## Normalized bundles

`NormalizedVacancy` preserves source facts and structured text blocks with provenance.
It does not classify primary role, requirements, importance or suitability.

`NormalizedResume` represents a full resume version, including experience entries,
explicit projects and preferences. Binding/target policy remains separate from resume evidence.

`validate_bundle_for_ref()` verifies that a loaded normalized object matches its current
pointer: identity, account scope, RAW provenance, semantic hash, contract versions,
materialization key, readiness and the externally computed normalized object SHA-256.

## Target policy and binding snapshots

The processing manifest pins a semantic `BindingSnapshot` and an immutable `TargetPolicy`.
PostgreSQL surrogate IDs are deliberately excluded from the binding snapshot/fingerprint;
logical account/binding/resume identities survive a clean database rebuild.

The policy body is canonical JSON with its own SHA-256 and schema/version. P2-03 may add
typed policy interpretation without changing the P2-01 snapshot envelope.

Application-only settings such as `auto_apply` and discovery-only `query_set_keys` are not
part of the semantic processing snapshot and therefore do not force expensive rematching.

## Input manifest and fingerprint

`ProcessingInputManifest` contains only semantic/replay inputs:

- vacancy normalized ref;
- resume normalized ref;
- binding snapshot;
- target policy snapshot;
- processing/model version bundle;
- explicit `as_of` evaluation epoch.

It intentionally excludes job IDs, claim timestamps, lease state and artifact locations.
The canonical JSON body is hashed with SHA-256 to produce `input_fingerprint`, matching the
existing `careerops_v2.processing_jobs` foundation.

## Jina boundary

The final version bundle already has an optional `JinaVersionBundle` containing the model,
code, tokenizer, runtime, rendering/selection/block protocol and token budget identity.

Jina runs in a separate `careerops-reranker` container on `edge`. Processing talks to a stable
reranker API; the model runtime behind that API may be vLLM, Transformers/PyTorch or another
validated backend without changing Processing business logic.

P2-01 does not choose or start the Jina runtime.

## careerops-processing runtime boundary

`src/careerops_processing/service` is the executable standalone Python service shell.

Permanent runtime configuration includes:

- PostgreSQL DSN;
- S3 endpoint and normalized/artifact buckets;
- reranker endpoint on edge;
- matching-core gRPC target on core;
- worker identity;
- local health bind address.

The Docker service is defined under `infra/compose/processing` and runs as a non-root,
read-only, capability-dropped container. Host networking is intentional: it needs direct LAN
access to edge while talking to matching-core over core loopback.

`/healthz` is a permanent liveness endpoint. `/readyz` deliberately remains unavailable until
P2-02 attaches the real queue worker lifecycle, so the foundation cannot claim to be a ready
Processing worker before it actually processes jobs.

## C++ matching-core boundary

The previous in-process pybind11 direction is superseded.

The production native boundary is the separate stateless `careerops-matching-core` service:

```text
careerops-processing
  orchestration / DB / S3 / Jina
        |
        | typed gRPC batch
        v
careerops-matching-core
  deterministic C++20 computation
        |
        v
careerops-processing
  artifact + PostgreSQL publication
```

The C++ service lives under `services/careerops-matching-core` and has its own Docker image.
It is deployed on `core`, where the Ryzen 7 5800X owns CPU-heavy deterministic work.

The matching core must never own:

- PostgreSQL connections or transactions;
- S3 reads/writes;
- Spark/JVM data processing;
- Jina/model-serving calls;
- job claims, leases, retries or artifact publication;
- HH/application side effects.

The permanent control plane is gRPC + Protobuf. P2-01 implements `Health` and
`GetCapabilities` in `proto/careerops/matching_core/v1/control.proto`.

The final evaluation RPC is intentionally not invented in P2-01. Its request/response types
must be derived from the frozen Requirement/ResumeEvidence and post-Jina qualification model.
Until then the service reports `evaluate_match_available=false` and
`evaluate_batch_available=false`.

This is a capability contract, not a temporary fake matcher.

## Network boundary on core

Both Processing and matching-core use host networking, but matching-core listens on
`127.0.0.1:50051` by default. The Python container therefore reaches it over host loopback and
the gRPC service is not exposed to the LAN.

Binding matching-core to a non-loopback address requires a separate transport security design;
P2-01 does not silently expose insecure gRPC remotely.

## Existing PostgreSQL foundation

P2-01 reuses the already defined v2 tables:

- `processing_jobs`;
- `match_results`;
- `application_candidates`.

No parallel processing schema is introduced.

The current v2 domain provenance still describes RAW/current materialization and does not
yet expose the full normalized object ref defined here. That producer-side materialization
is an external Spark/domain prerequisite and is intentionally not implemented in P2-01.

## P2-01 Definition of Done

- `careerops_processing` exists as a separate package and executable service boundary;
- a standalone Processing Docker image/Compose service exists for core;
- normalized vacancy/resume contracts are strict, immutable and source-provenanced;
- normalized pointers distinguish raw/normalized/semantic hashes;
- target policy and binding snapshots are immutable/versioned;
- the input manifest produces deterministic fingerprints;
- bundle/ref identity can be verified without knowledge of Spark internals;
- reason-code namespace is reserved for later filter/matcher stages;
- `careerops-matching-core` exists as a separate C++20 Docker service on core;
- matching-core has a versioned permanent gRPC control protocol;
- no evaluation wire schema is published before Requirement/Evidence semantics are frozen;
- Python reference semantics remain independent of I/O and are the future C++ parity oracle;
- contract/runtime-config tests cover the service foundation.
