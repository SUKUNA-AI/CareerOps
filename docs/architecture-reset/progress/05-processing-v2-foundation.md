# Processing v2 foundation (P2-01)

Status: implementation foundation for `ar/processing-v2`.

## Scope

P2-01 creates the permanent service/contract boundary for `careerops-processing`.
It does **not** implement Spark, queue workers, filtering, Jina, scoring or APPLY.
Those layers build on the contracts introduced here.

## Ownership boundary

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
The policy body is canonical JSON with its own SHA-256 and schema/version. P2-03 may
introduce typed filter-policy interpretation without changing the P2-01 snapshot envelope.

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

The manifest rejects:

- vacancy/resume type swaps;
- non-ready normalized refs;
- resume/account/binding identity mismatch;
- target-policy mismatch;
- dictionary-version mismatch;
- naive `as_of` timestamps;
- an `as_of` that predates either pinned source observation.

## Jina version slot

The final version bundle already has an optional `JinaVersionBundle` containing the model,
code, tokenizer, runtime, rendering/selection/block protocol and token budget identity.
P2-01 does not call Jina. P2-05 fills this slot for rerank-dependent processing releases.

## Native boundary

`src/careerops_processing/native/README.md` reserves the future C++20 + pybind11 seam.
There is intentionally no C++ code in P2-01. Python remains the reference implementation;
only measured pure-compute hot paths may later move behind the same service boundary.

The native layer may never own PostgreSQL, S3, Spark, HTTP/Jina, leases or artifact
publication.

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

- `careerops_processing` exists as a separate package boundary;
- normalized vacancy/resume contracts are strict, immutable and source-provenanced;
- normalized pointers distinguish raw/normalized/semantic hashes;
- target policy and binding snapshots are immutable/versioned;
- the input manifest produces deterministic fingerprints;
- bundle/ref identity can be verified without knowledge of Spark internals;
- reason-code namespace is reserved for later filter/matcher stages;
- future C++ seam is documented but contains no native implementation;
- contract tests cover unknown semantics, identity scope, policy hashing, deterministic
  fingerprints, readiness and bundle/ref integrity.
