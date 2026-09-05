# CAR-142 — Shared test support

Scope: proven fixture, builder and test-double duplication, following the
[CAR-140 audit](TEST_SUITE_AUDIT.md) and
[CAR-141 execution boundaries](TEST_SUITE_BOUNDARIES.md).
All 24 main-suite test modules and root `conftest.py` were reviewed.

## Shared contracts

| Support | Consumers under `tests/` | Contract and preserved differences |
|---|---|---|
| `support.hh.make_hh_vacancy` | `test_postgres_storage.py`, `test_postgres_integration.py` | Source vacancy payload used in storage/claim setup. Fresh mutable defaults per call; explicit field replacement, without normalization or deep merging. The integration module keeps a small `_vacancy` adapter specifying its title, description, area ID and publication timestamp. Both original payload shapes and values are preserved. |
| `support.s3.JsonWriteRef` | `test_hh_account_orchestration.py`, `test_hh_observe.py`, `test_hh_raw.py` | Frozen result with only `uri`. Each store still owns its payload capture, copying and metadata behaviour. Hash-bearing audit references remain separate. |
| `support.postgres.TransactionRecorder` | `test_backfill_hh_postgres.py`, `test_materialize_hh_pending.py`, `test_postgres_storage.py` | Append `begin`, then `commit` or `rollback` to the supplied event list; propagate exceptions. No SQL execution, saved state or rollback simulation. Each connection double retains its own query behaviour. |

These are ordinary Python helpers, imported through `support` from the main
test directory. They need no fixture lifecycle or global injection. The package
initializer contains no setup, imports or pytest hooks. Root `conftest.py` stays
responsible only for the existing event-loop and temporary-directory mechanics.

For the three selected families, the baseline was **8 local implementations**:
2 vacancy payload builders, 3 URI reference classes and 3 transaction recorders.
There are now **3 shared implementations and 1 local vacancy variant adapter**
(4 helper definitions total). The common contracts each have one owner; five
redundant copies of those contracts were removed. Shared support consists of
3 domain modules plus the package initializer, previously none.

## Deliberately local families

| Candidate family | Reason to keep separate |
|---|---|
| Other HH vacancy builders and inline mapper payloads | Cover-letter, filtering, application-guard and ETL tests require different fields or omissions. A common full vacancy would hide those differences. Large versioned payload cleanup belongs to CAR-143. |
| Resume, account, binding and application-identity setup | Published status, lifecycle, auto-apply, query-set overlap and identity provenance differ. Registry, audit and orchestration scenarios do not share one builder contract. |
| Timestamps, IDs and runtime guards | Equal literals often serve different roles, such as completed versus pending runs. Keep scenario identities and explicit write permissions visible instead of creating shared global defaults. |
| S3/RAW stores and `S3ObjectRef` construction | Stores retain a payload reference, a deep copy, a last value, a metadata map or a computed digest. ETL's object loader and S3's client/paginator model different ports. The full object-ref builder has one consumer module. Only the identical URI result is shared. |
| ETL sinks and transaction state | The lightweight sink records calls; `IdempotentTransactionalSink` models keyed state, and `SnapshotTransaction` restores that state on failure. They must not become modes of the event recorder. Batch/run/candidate builders remain local to their versioned ETL scenarios. |
| PostgreSQL cursors, connections and real database helpers | Fixed-row, sequenced-row and materializer cursors have different contracts. Fake SQL recording is separate from actual psycopg reads, database creation, migrations and DSN validation. No real PostgreSQL fixture is extracted. |
| Application audit stores, claim stores and drivers | Claim ownership/locking, submitted-pair evidence, failure simulation and real-connection transaction-idle checks are distinct behaviours. Similar method names do not make these interchangeable. |
| Alembic/schema helpers | The two one-line whitespace helpers offer little readability benefit from extraction. Migration recorders and explicit schema expectations have separate ownership; expected schemas remain independent test oracles. |

## Adding shared support

1. Identify at least two real consumer modules, or prove repetition of the same
   semantic contract. Compare outputs, mutation/aliasing, failure behaviour and
   lifecycle before extracting; matching names or imports are insufficient.
2. Use a narrow domain module. Keep one-consumer helpers local; do not create
   `common.py`, a universal infrastructure fake or a root-conftest catalogue.
3. Prefer small pure builders. Make scenario-relevant fields explicit, create
   fresh mutable defaults, and preserve malformed overrides. Do not add hidden
   normalization, inference, deep-merge rules or expected-result algorithms.
4. Use pytest fixtures only for lifecycle/injection. A fake should implement
   only the required port, with simpler semantics than production. Keep a
   recorder separate from a stateful model; keep assertions in the tests.
5. Compare collected cases and execution selections, run FAST, and check helper
   equivalence. An extraction must not require changed expectations. Review any
   payload-shape or state-model difference before accepting a new consumer.

## Execution and scope

Sharing a pure builder with a PostgreSQL test does not change execution layers:
FAST still needs no service, network or credentials; real database cases retain
`integration_postgres`. Vendored `hh-applicant-tool` tests remain separate.
The CAR-142 baseline is **219 collected / 208 FAST / 11 PostgreSQL collected**;
these are measurements for this refactoring, not permanent suite quotas.

Destructive PostgreSQL setup is unchanged. CAR-140's potential `host`/`hostaddr`
target-validation gap remains a separate bug. The CAR-141 requirement for one
fail-closed target-validation boundary before future PostgreSQL CI still applies.

CAR-142 does not change production, CI, markers, migrations, test expectations
or semantic coverage. It retains `RETIRE_AFTER_V2` tests and leaves oversized
payload cleanup, test retirement, module splitting and coverage/flaky policy to
CAR-143, CAR-144, CAR-145 and CAR-146 respectively.
