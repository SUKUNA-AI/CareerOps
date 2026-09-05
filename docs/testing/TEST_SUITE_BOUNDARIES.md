# CAR-141 — Fast and integration test suite boundaries

Feature: **CAR-139 — Test suite architecture and cleanup**.
Defined on **2026-09-05**, following the [CAR-140 semantic audit](TEST_SUITE_AUDIT.md).

## 1. Purpose

Define how the main CareerOPS suite is selected for execution and what each
selection can prove. Execution dependency and semantic test type are separate
dimensions. **FAST does not mean UNIT.**

This policy covers the existing suite and the admission rules for future tests.
CAR-141 enforces marker-name validation through
[`pyproject.toml`](../../pyproject.toml); it leaves test implementations and the
current CI workflow unchanged. The audit remains the source for individual
behavioral gaps and proposed cleanup decisions.

## 2. Current measured baseline

CAR-140 measured the following main-suite baseline on 2026-09-05:

| Measurement | Baseline | Meaning |
|---|---:|---|
| Total collected cases | 219 | Main `tests/` suite, including parametrized cases |
| FAST cases | 208 | Passed with `-m "not integration_postgres"` |
| INTEGRATION_POSTGRES cases | 11 | Collected with `-m "integration_postgres"`; not executed in the audit |

The audit contains 24 test modules: the two PostgreSQL integration modules own
the 11 marked cases, and the remaining 22 modules own the 208 FAST cases.
Vendored tests are outside these measurements.

These counts are **baseline metrics, not permanent invariants, quotas, or CI
thresholds**. Meaningful additions and later approved cleanup can change them.
CAR-141 is expected to preserve this baseline because it does not change tests.
Collection, passing execution, skips, and deselection must be reported separately;
collecting PostgreSQL cases is not evidence that they pass against PostgreSQL.

## 3. Execution layer versus semantic test type

| Dimension | Question answered | Representation |
|---|---|---|
| Execution layer | Which services, network access, credentials, and setup does this case actually require? | Current FAST selection or the `integration_postgres` marker |
| Semantic test type | Which behavior or contract is proved, and across which code boundaries? | Module/domain organization, descriptive names, and documentation |

Unit tests exercise focused logic; contract tests check an interface or data
agreement; component tests exercise a workflow or adapter with controlled ports.
Any of these can belong to FAST when their actual dependencies meet section 4.
A schema contract can instead require PostgreSQL when its assertion depends on
the database accepting or rejecting real rows.

Classify by the executed test body, fixtures, helpers, subprocesses, and assertion
boundary. A filename, a function named `test_integration_*`, an imported
production module, or a word such as `atomic` or `rollback` is insufficient.
Recorded SQL, predefined rows, and in-memory state restoration do not establish
real database effects.

Do not introduce `unit`, `component`, `contract`, or domain pytest markers.
Built-in and installed-plugin markers such as `parametrize` and `asyncio` remain
valid test mechanics; they are not additional execution layers.

## 4. FAST definition

A FAST test:

- requires no external service, network access, or credentials during collection
  or execution;
- does not require PostgreSQL, SeaweedFS/S3, or HH to be available;
- can run on an isolated GitHub-hosted runner after installing CareerOPS with
  its dev dependencies;
- may use local filesystem operations, temporary files, local subprocesses,
  in-memory objects, fakes, mocks, and other test doubles.

Local processes qualify only when their entire execution also needs no service,
network, or credentials. Offline Alembic SQL generation qualifies; a subprocess
that connects to a local database does not. Dummy DSN or credential-shaped strings
used solely as validation inputs do not create a credential requirement.

FAST currently means the main `tests/` selection with
`-m "not integration_postgres"`. There is no separate `fast` marker. This
selection includes unit, contract, fake-backed component, filesystem, and local
subprocess checks. Elapsed time alone does not determine membership.

Do not conceal a service dependency with a conditional skip or opportunistic
connection in an otherwise FAST case. Boundary membership must be explicit and
stable across developer machines and CI.

## 5. INTEGRATION_POSTGRES definition

A test belongs to INTEGRATION_POSTGRES when it connects, directly or through
fixtures/helpers/subprocesses, to an **actual PostgreSQL instance** and exercises
real DDL/DML, constraints, transactions, locking, concurrency, or persisted state.
Its execution marker is `integration_postgres`.

The current cases are in:

- [`test_postgres_integration.py`](../../tests/test_postgres_integration.py):
  runtime storage, claims, query cursor, and transaction scenarios using real
  psycopg connections;
- [`test_alembic_postgres_integration.py`](../../tests/test_alembic_postgres_integration.py):
  fresh/legacy migration paths, schema comparison, controlled drift, and a fresh
  database downgrade/upgrade round trip.

These tests can still use fakes for other ports, including HH or S3. A fake HH
driver does not remove the real PostgreSQL dependency, and passing these cases
does not establish real HH or S3 compatibility.

Use only an explicitly configured disposable test target, currently provided by
`CAREEROPS_TEST_POSTGRES_DSN` and restricted to local PostgreSQL. There must be no
fallback to `CAREEROPS_POSTGRES_DSN`, other runtime/production configuration, or
an implicitly discovered database. Destructive operations require the policy in
section 11.

The current fixtures skip when the dedicated test DSN is missing. This behavior
is preserved; it does not make the cases FAST. Collecting this boundary does not
need a running database and must not connect or perform destructive setup.
CAR-141 validates PostgreSQL collection only.

## 6. Vendor hh-applicant-tool boundary

`hh-applicant-tool/tests/` is the vendored upstream project's own suite. It is
outside the main CareerOPS 219-case baseline and outside the main
`testpaths = ["tests"]` discovery boundary.

Keep upstream suite execution, dependencies, and results separate. Do not add its
path to main `testpaths`, silently aggregate its counts into CareerOPS results,
or assume installing CareerOPS dev dependencies installs the upstream test
environment. Any upstream validation needs a separately scoped invocation.

CareerOPS-owned adapter/bridge contracts remain in the main suite. They qualify
as FAST when they exercise CareerOPS behavior through controlled upstream ports
without running live HH requests or the upstream application's test suite.

## 7. Future INTEGRATION_S3 / E2E policy

| Future boundary | Intended dependency and evidence | Admission condition |
|---|---|---|
| INTEGRATION_S3 | Actual disposable SeaweedFS/S3; real write/readback, metadata, integrity, and RAW immutability behavior | Explicit endpoint, disposable namespace, scoped credentials, ownership/cleanup, reproducible setup, and separate execution selection |
| E2E | Explicitly scoped critical paths across the implemented system, with each real and substituted dependency declared | Defined entry/exit points and observable outcomes, supported infrastructure, isolated data, and an explicit external-side-effect policy |

These are documentation-only future boundaries. **Do not register
`integration_s3` or `e2e` markers until their corresponding infrastructure
exists.** CAR-141 adds no tests, fixtures, service setup, CI jobs, or placeholder
markers for them. A current fake-backed workflow is still FAST even if it spans
several components.

When a new boundary is implemented, update its documentation, marker registry,
local commands, and CI selection together. The present negative FAST selector
would otherwise include a newly marked S3/E2E case. Explicitly exclude every
implemented external boundary from FAST in the same change; do not rely on
missing credentials to skip it. E2E scope does not itself authorize live HH
submissions or use of production data.

## 8. Classification examples from current tests

The dependencies and assertions below determine classification; the paths only
locate the examples.

| Current example | Execution boundary | Evidence and its limit |
|---|---|---|
| `test_hh_cover_letters.py` | FAST | Pure letter-generation decisions; no external transport |
| `test_postgres_storage.py` with `FakeConnection` | FAST | SQL/parameter recording, row decoding, and guards; SQL is not executed by PostgreSQL |
| `test_postgres_integration.py` with actual psycopg connections | INTEGRATION_POSTGRES | Real SQL, constraints, transactions, and multi-connection claims; HH remains fake |
| `test_alembic_postgres_integration.py` | INTEGRATION_POSTGRES | Actual migration DDL and live catalog state |
| Offline subprocess cases in `test_alembic_baseline.py` | FAST | Local Alembic CLI/configuration and generated SQL without an actual database |
| DSN validation and substituted I/O in `test_alembic_cutover.py` | FAST | Guard decisions and orchestration; parsing a DSN is not connecting to it |
| `test_storage_schema.py` | FAST | Declarative metadata contracts; not live constraint enforcement |
| `test_s3_listing.py` with `FakeS3Client`, or a fake S3 store | FAST | Adapter requests, serialization, and metadata handling; not real SeaweedFS behavior |
| `test_hh_s3_to_postgres.py` with fake storage/sinks | FAST | ETL contracts and component behavior; dictionary replay/snapshot rollback is not PostgreSQL proof |
| `test_integration_*` cases in `test_hh_account_orchestration.py` | FAST | Account workflow with fake ports despite the function names |
| JSON registry checks in `test_hh_resume_sync.py` and local state checks in `test_scheduler_dispatcher.py` | FAST | Local filesystem persistence with controlled external ports |

A future test writing to and reading from an actual disposable SeaweedFS target
belongs to INTEGRATION_S3 once that boundary is implemented.

The audit identified misleading semantic signals in current tests: fake-backed
cases with integration/atomicity/rollback names, and a real PostgreSQL test whose
ETL helper copies orchestration instead of invoking the actual loader. These
limits affect what a passing assertion proves, not which service is required.
CAR-141 preserves their current execution membership and leaves rewrites to
separately scoped work.

The offline Alembic subprocess checks clear `PYTHONPATH` and run outside the
checkout. Their package-source provenance therefore depends on the active
installation. Use an environment tied to the worktree under test: a shared
editable environment pointing elsewhere can exercise another checkout's package.
This environment concern does not change their FAST classification.

## 9. Rules for adding a new test

1. State the behavior, owning domain, observable outcome, and failure mode. Choose
   the semantic type in module/domain organization and documentation.
2. Inspect every dependency reachable through setup, fixtures, helpers, and
   subprocesses. Keep imports and collection free of service connections,
   credential requirements, and destructive work, including for marked modules
   imported before `-m` deselection.
3. Use FAST when all requirements in section 4 hold. Keep temporary state local
   and isolated, and use explicit fake/sentinel ports for external dependencies.
4. Mark every actual PostgreSQL case `integration_postgres` and follow section
   11. Use a module-level mark only when every case shares that dependency;
   otherwise mark the relevant cases explicitly. Never classify solely by path
   or imported module.
5. If another external dependency is required, establish its boundary under
   section 10 before admitting the case to the main suite. Do not add an unmarked
   network test or a placeholder future marker.
6. Verify main collection and the relevant selections. Record deliberate count
   changes and evidence gained; do not preserve 208/11 by dropping useful tests
   or relabeling their dependencies. Keep expected data independent of the
   production algorithm it checks.

## 10. Rules for adding a new external integration

Introduce an external boundary only with a concrete real-service assertion and
reproducible infrastructure. Define its service/version requirements, explicit
test configuration, readiness/failure handling, isolated disposable resources,
credential scope, ownership, and cleanup before enabling execution.

Configuration must fail closed for an unsafe or ambiguous target. There must be
no runtime/production fallback, automatic discovery of a usable live service, or
destructive work during collection. Provide service-free negative validation
checks as well as real-service checks of the intended behavior.

Register a precisely described execution marker with the infrastructure and
update all FAST exclusions and dedicated invocation/CI rules atomically. A
required integration job must fail when its target is missing or unusable,
rather than report success after silently skipping every case. Document what
remains fake so the result does not overstate end-to-end evidence.

## 11. PostgreSQL destructive-test safety requirements

**Known unresolved gap (CAR-140 R05):** `clean_postgres_dsn` in
`tests/test_postgres_integration.py` validates `host` but retains an unchecked
`hostaddr`. A DSN with `host=localhost` and a remote `hostaddr` can reach the
destructive database-creation path. The Alembic disposable-target validator
rejects that combination, so the two fixture paths apply different policies.
CAR-140 demonstrated this with a sentinel before connection; it did not perform
a destructive operation on a remote server. **CAR-141 does not fix this gap.**

Before future PostgreSQL CI is enabled, destructive integration infrastructure
must have **one fail-closed target-validation boundary** shared by every
destructive entry point. This is a prerequisite, not a claim that the current
fixtures already satisfy it. The boundary must enforce all of the following:

- Require an explicit disposable test target and validate the effective target
  before any destructive connection or setup. The current local-only policy
  must cover both `host` and `hostaddr`, including conflicts and multi-host
  forms. Reject unsafe, missing, or ambiguous values.
- Account for connection resolution inputs, including service settings and
  environment defaults: they must not override or bypass the approved host,
  address, database, or role. Reject unresolved ambiguity rather than trusting
  a friendly database name or `host=localhost` alone.
- Never fall back to runtime/production DSNs. Pin the validated target into
  psycopg, Alembic, derived administrative/test connections, and cleanup so no
  later URL or environment precedence can redirect execution.
- Establish exclusive ownership and an explicit destructive scope. Localhost
  alone does not prove disposability. Administrative connections may act only
  on the owned disposable database; schema resets may affect only approved
  owned objects. Shared runtime databases are not test targets.
- Prevent cross-run or cross-worker destruction. Current fixed-name database
  recreation is not safe to parallelize without isolated ownership or explicit
  serialization. A shared fixture alone does not solve this problem.
- Apply the same validation and ownership constraints to teardown and failure
  cleanup. Never broaden cleanup to an unvalidated target. Keep fresh-only
  downgrade scenarios separate from populated legacy stamp/upgrade scenarios.
- Prove rejection of unsafe DSNs, runtime fallback, and precedence overrides
  without a database connection, then verify isolated real setup/cleanup with
  the implemented infrastructure. Make required CI fail if setup cannot be
  validated or executed.

The host/hostaddr repair belongs in a separate Bug/Task before PostgreSQL CI or
destructive fixture expansion. Neither strict marker validation nor a collected
integration suite establishes target safety.

## 12. Relationship with current CI

The existing [CI workflow](../../.github/workflows/ci.yml) runs for pull requests
to `main`. Its FAST job uses GitHub-hosted Ubuntu runners, installs `.[dev]`, and
selects `-m "not integration_postgres"` on Python 3.12 and 3.13. The Python 3.12
quality job runs Ruff, mypy, and dependency consistency checks. There is no
PostgreSQL integration gate today.

CAR-141 preserves `pythonpath = ["src"]`, `testpaths = ["tests"]`, and the current
selection. It adds `addopts = ["--strict-markers"]` and strengthens the existing
`integration_postgres` description. Unknown or misspelled marker annotations
encountered during collection now raise errors instead of warnings, including
in CI through the shared configuration. The default pytest invocation still
discovers both current boundaries; use the explicit selector for FAST.

Strict marker validation checks registered marker names, not actual I/O. It does
not detect an unmarked network dependency or validate the spelling of a `-m`
expression. Dependency review and checking collection/selection results remain
necessary. No network sandbox or CI change is introduced by CAR-141.

Run these checks from the repository root in an environment with CareerOPS dev
dependencies installed:

```text
python -m pytest tests --collect-only -q -p no:cacheprovider
python -m pytest tests -q -p no:cacheprovider -m "not integration_postgres"
python -m pytest tests -q -p no:cacheprovider -m "integration_postgres" --collect-only
python -m pytest --markers
python -m ruff check src scripts tests
python -m mypy src
```

For CAR-141, compare the results with section 2 and verify that only this document
and `pyproject.toml` changed. These commands execute FAST and collect PostgreSQL
cases; they do not require a database or verify live PostgreSQL behavior.

## 13. Relationship with CAR-142–CAR-146

The [audit's follow-up plan](TEST_SUITE_AUDIT.md) remains scoped as follows:

| Task | Follow-up responsibility | Boundary rule carried forward |
|---|---|---|
| CAR-142 — Consolidate duplicated test fixtures and builders | Share only proven equivalent infrastructure | Keep specialized fakes local when their semantics differ; shared fixtures must not introduce external dependencies into FAST or hide the separate target-safety repair |
| CAR-143 — Replace oversized inline payloads with minimal fixtures | Reduce setup noise while preserving versioned/golden contracts | Keep synthetic/source provenance and expected values explicit; smaller fixtures do not change an execution layer |
| CAR-144 — Remove redundant and implementation-detail tests | Replace weak evidence and consolidate only with surviving invariant coverage | Real SQL replacements require INTEGRATION_POSTGRES; implement adequate replacements before removing old coverage, and retain RETIRE_AFTER_V2 cases until their cutover |
| CAR-145 — Split oversized HH test modules by behavior | Organize coherent behaviors/domains | Semantic organization must preserve execution membership; do not turn unit/component/contract/domain labels into CI markers |
| CAR-146 — Establish measured coverage duration and flaky-test policy | Measure the resulting suite and define maintenance policy | Report counts, durations, skips/deselections, and flakiness by implemented execution boundary; avoid arbitrary count/coverage quotas or silent required-integration skips |

CAR-141 supplies definitions and marker validation for these tasks. It does not
perform their fixture, payload, assertion, layout, measurement-policy, or CI work.

## 14. Explicit non-goals of CAR-141

- Delete, rewrite, move, or split tests/modules, or consolidate fixtures/builders.
- Change production behavior or repair production bugs found by CAR-140.
- Modify Alembic revisions, SQL migrations, or their historical contracts.
- Repair the PostgreSQL host/hostaddr target-validation gap in this task.
- Add PostgreSQL/SeaweedFS infrastructure, live service runs, E2E tests, or CI jobs.
- Register future, semantic, or domain markers, or merge vendored tests into the
  main suite.
- Establish permanent test-count targets, coverage percentages, duration limits,
  or flaky-test retry policy.
- Change any file beyond `docs/testing/TEST_SUITE_BOUNDARIES.md` and
  `pyproject.toml`, or commit or push this work.
