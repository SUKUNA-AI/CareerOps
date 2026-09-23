# CareerOPS final-freeze test inventory

This document records the test-suite decision made for the parked CareerOPS repository.
The goal is not to minimize file count. The goal is to remove tests tied to deleted runtime implementations while keeping distinct contract, orchestration, persistence and physical-boundary coverage.

## Verdict legend

- **KEEP**: still protects a live production boundary or invariant.
- **KEEP / layered**: looks similar to another suite but exercises a different layer, so it is not redundant.
- **REMOVED**: obsolete after an implementation cutover or architecture reset.
- **REWRITTEN**: the scenario is useful, but the old assertion tested implementation layout rather than behavior.

## Test infrastructure

- `tests/conftest.py` — **KEEP**. Marker registration and shared PostgreSQL test-target wiring.
- `tests/support/postgres.py` — **KEEP**. Enforces disposable PostgreSQL targets and prevents accidental use of a runtime database.
- `tests/support/processing.py` — **KEEP**. Typed builders used by Processing contract/orchestration tests.

## Database schema and migration tests

- `tests/test_alembic_migrations.py` — **KEEP**. Static migration-chain and revision invariants.
- `tests/test_alembic_metadata_consistency_postgres.py` — **KEEP / layered**. Detects SQLAlchemy metadata drift against a migrated PostgreSQL schema.
- `tests/test_alembic_postgres_integration.py` — **KEEP / layered**. Exercises real PostgreSQL migration behavior.
- `tests/test_postgres_ci_integration.py` — **KEEP**. Proves the CI PostgreSQL target is reachable and usable.
- `tests/test_postgres_test_target.py` — **KEEP**. Safety contract for test DSN selection; deliberately separate from schema tests.

These files are not redundant: static revision checks, metadata drift, real migrations and CI target safety fail for different reasons.

## HH source adapter

- `tests/test_hh_configuration.py` — **KEEP**. Multi-account/config parsing and validation.
- `tests/test_hh_discovery_contract.py` — **KEEP**. Search/discovery configuration contract.
- `tests/test_hh_driver_readonly.py` — **KEEP**. Prevents applicant-side write/submit commands from entering the source adapter.
- `tests/test_hh_adapter_watermarks.py` — **KEEP**. Lossless paging/watermark behavior.

The adapter remains parked behind the explicit `collect` Compose profile, but its read-only and persistence contracts are part of the reusable artifact.

## RAW / lake normalization

- `tests/test_normalizer_spark_contract.py` — **KEEP**. Feeds representative full HH vacancy/resume payloads through the PySpark normalizer core and validates the produced JSON with the exact strict `NormalizedVacancy` / `NormalizedResume` Processing contracts.
- `tests/test_s3_listing.py` — **KEEP**. Logical/physical S3 prefix listing and pagination behavior used by immutable RAW discovery.
- `tests/integration/storage/test_seaweedfs_s3.py` — **KEEP / physical boundary**. Real S3-compatible SeaweedFS integration; not replaceable by unit-level listing tests.

## Processing contracts, queue and service shell

- `tests/test_processing_contracts.py` — **KEEP**. Cross-component Pydantic contracts and normalized references.
- `tests/test_processing_queue.py` — **KEEP**. Durable Processing queue semantics.
- `tests/test_processing_worker.py` — **KEEP**. Worker lifecycle, lease and execution orchestration.
- `tests/test_processing_service_config.py` — **KEEP**. Environment/config validation.
- `tests/test_processing_service_runtime.py` — **KEEP / layered**. Runtime composition/readiness behavior, including external Jina and matching-core boundaries.
- `tests/test_processing_postgres_integration.py` — **KEEP / persistence boundary**. Real Processing queue/current-state PostgreSQL behavior.
- `tests/test_processing_semantic_registry_postgres.py` — **KEEP**. P2-04 reusable semantic artifact registry behavior.
- `tests/integration/e2e/test_processing_baseline.py` — **KEEP / E2E**. Crosses the Processing orchestration boundary end to end and therefore is intentionally broader than unit suites.

## P2-03 deterministic high-recall filtering

- `tests/test_processing_filter_policy.py` — **KEEP**. Target-policy parsing and fixed/tunable filtering configuration boundary.
- `tests/test_processing_filtering.py` — **KEEP**. Core deterministic filter behavior.
- `tests/test_processing_filtering_ambiguity_guards.py` — **KEEP / regression**. Ambiguous source facts must not become unsafe hard exclusions.
- `tests/test_processing_filtering_false_exclusion_guards.py` — **KEEP / regression**. Explicit high-recall false-exclusion protection.

The two guard suites are not duplicate happy-path tests; they encode failure classes that motivated the high-recall design.

## P2-04 requirement/evidence extraction

- `tests/test_processing_p204.py` — **KEEP**. Main RequirementSet / ResumeEvidenceSet extraction semantics.
- `tests/test_processing_p204_hardening.py` — **KEEP / regression**. Provenance, ambiguity and hardening cases not represented by the main happy-path suite.

## P2-05 Jina selection

- `tests/test_processing_p205.py` — **KEEP**. Rendering/selection/version-bundle semantics.
- `tests/test_processing_p205_artifact_publisher.py` — **KEEP / layered**. Immutable artifact hashing/publication contract.
- `tests/test_processing_p205_executor.py` — **KEEP / layered**. Processing executor orchestration around Jina and P2-05 artifacts.
- `tests/test_processing_reranker_http.py` — **KEEP**. HTTP client/runtime identity and failure classification.

These are separate semantic, artifact, orchestration and transport layers.

## P2-06 / P2-07 native cutover

- `tests/test_processing_p206_p207_contracts.py` — **KEEP**. Python-side wire/artifact validation consumed by orchestration.
- `tests/test_processing_p206_p207_artifact_publisher.py` — **KEEP / layered**. S3 artifact linkage and hashes after native evaluation.
- `tests/test_processing_p206_p207_executor.py` — **KEEP / layered**. Python → C++ gRPC orchestration and returned-contract validation.
- `tests/test_processing_p207_audit.py` — **KEEP**. Replay/audit invariants and pinned artifact semantics.
- `tests/test_processing_p207_postgres_integration.py` — **KEEP / persistence boundary**. Current result/candidate publication and fencing on real PostgreSQL.
- `tests/integration/matching_core/test_control_contract.py` — **KEEP / native boundary**. Compiled C++ service readiness/capabilities.
- `tests/integration/matching_core/test_date_validation.py` — **KEEP / native regression**. Calendar validation and experience-date behavior in C++.
- `tests/integration/matching_core/test_decision_contract.py` — **KEEP / native semantics**. Actual P2-06 qualification and P2-07 scoring/decision behavior through gRPC.

The Python P2-06/P2-07 files above do not reimplement scoring. They verify orchestration and persisted contracts. Decision semantics are owned and tested in the compiled C++ integration suite.

### Removed obsolete P2-06 / P2-07 coverage

- `tests/test_processing_p206_p207.py` — **REMOVED** with the Python qualification/scoring implementation.
- `tests/test_processing_p207_structured_scoring.py` — **REMOVED** with the Python scorer.
- `tests/test_processing_p205_manifest_cutover.py` — **REMOVED** after the cutover state became the only supported runtime architecture.

Reintroducing these files would recreate dual-oracle coverage and is intentionally blocked by the architecture tests.

## Calibration

- `tests/test_processing_calibration.py` — **KEEP**. Dataset/provenance/split/reporting contracts.
- `tests/test_processing_calibration_p205_runner.py` — **KEEP / layered**. P2-05 calibration execution path.
- `tests/test_processing_calibration_policy_search.py` — **KEEP / layered**. P2-07 policy search, recall-floor and ranking behavior.

The committed calibration manifest also has its own dedicated CI gate; that gate does not replace the Python calibration tests.

## Reranker observability and quality

- `tests/test_reranker_runtime.py` — **KEEP**. HTTP server/runtime identity and health behavior.
- `tests/test_reranker_audit.py` — **KEEP**. Operational audit persistence and selection observability.
- `tests/test_reranker_metrics.py` — **KEEP**. Prometheus runtime-series exposition (`requests`, errors, latency, tokens, document counts).
- `tests/test_reranker_quality_metrics.py` — **KEEP / not redundant**. Offline ranking quality metrics (`Hit@k`, `Recall@k`, `Precision@k`, RR, AP/MAP building block, NDCG).

`test_reranker_metrics.py` and `test_reranker_quality_metrics.py` share the word "metrics" only; one tests operational telemetry, the other evaluates ranking quality.

## Application Owner / P2-08

- `tests/test_application_owner.py` — **KEEP**. Owner state-machine behavior with fakes.
- `tests/test_application_owner_architecture.py` — **KEEP**. Ownership and dependency boundary checks.
- `tests/test_application_owner_runtime_contract.py` — **KEEP**. Runtime configuration/deadline constraints.
- `tests/test_application_transport_facade.py` — **KEEP**. Applicant transport normalization, timeout and error classification.
- `tests/test_application_owner_hardening.py` — **KEEP, REWRITTEN**. Keeps transport-safe retry and explicit SQL fence checks, but no longer asserts that rebind code lives in one specific file.
- `tests/test_application_owner_postgres_integration.py` — **KEEP / persistence state machine**. Permanent guard, pre-submit stale fencing, reused-candidate rebind, safe-failure rebind, lease expiry and submit fencing on PostgreSQL.
- `tests/test_application_owner_concurrency_postgres.py` — **KEEP / concurrency**. Advisory locks, account-wide gates and concurrent claim behavior; not duplicated by the serial integration suite.

The reused-candidate regressions deliberately cover both blocked pre-submit and `safe_failure` states because `claim_retry` runs before rebind and must be fenced to the exact `(candidate_id, processing_job_id)` pair.

## Repository architecture guards

- `tests/test_no_legacy_runtime.py` — **KEEP**. Prevents deleted legacy runtime paths and Python P2-06/P2-07 implementations from returning.
- `tests/test_project_dependencies.py` — **KEEP**. Dependency boundary/packaging contract.

## Final redundancy decision

No remaining test file is classified as removable solely for duplication. Apparent overlaps are layered contracts: pure semantics vs artifact publication vs executor orchestration vs PostgreSQL behavior vs compiled-service/real-storage integration.

The obsolete coverage found during final freeze was handled in two ways:

1. Python P2-06/P2-07 implementation tests were removed with the implementation.
2. The Application Owner hardening meta-test was rewritten after rebind logic moved into `postgres_rebind.py`; behavior is now protected by PostgreSQL regression tests instead of a file-layout assumption.

Future cleanup should remove a test only when its production boundary is removed or another test is shown to exercise the same failure mode through the same layer. Similar names are not sufficient evidence of redundancy.
