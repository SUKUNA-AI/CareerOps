# CAR-143 — Test fixture policy

Use the [audit](TEST_SUITE_AUDIT.md), [execution boundaries](TEST_SUITE_BOUNDARIES.md)
and [support ownership rules](TEST_SUPPORT_ARCHITECTURE.md) together. Fixture size
is diagnostic; preserving the tested invariant takes priority over line counts.

## Choose by the role of the data

| Category | Practical rule |
|---|---|
| **A. MINIMAL_SYNTHETIC** | Keep identity and fields needed by the scenario. Omit unrelated optional data only after checking the same invariant is still exercised. Small local literals or pure builders are sufficient; label realistic synthetic data as synthetic. |
| **B. NEGATIVE_SHAPE** | Show the missing field, deletion or malformed value in the case. Mutate a fresh fixture when a complete envelope is required. Do not restore missing fields or normalize invalid values in a builder/loader. |
| **C. VERSIONED_WIRE** | Keep the complete historical/source envelope, including cross-references and nested schema versions. Expected data must be independent of production serializers. Retirement of a producer does not retire decoding/replay compatibility. |
| **D. HASH_OR_SERIALIZATION_SENSITIVE** | Preserve the required body semantics, key order, number representation and metadata separation. If exact bytes are the contract, keep bytes without parse/re-serialization. Do not compute expected output with the production algorithm. |
| **E. LEGACY_ONLY** | Keep temporary filtering/OBSERVE/control-plane and migration setup local, or make a small safe reduction. Do not build a fixture framework around a path being replaced by Architecture Reset. |

Categories can overlap: a historical envelope can also be hash-sensitive, and a
negative case can corrupt a versioned fixture. Use the stricter preservation rule.

## Python builders and JSON files

- Use a small pure Python builder only when it reduces repeated setup. Keep
  scenario-defining fields explicit, create fresh mutable values, and keep
  search items, full vacancies, resumes, requests and results separate.
- Keep one-consumer helpers local. Shared support follows CAR-142's proven
  contract/reuse rule. Use pytest fixtures only for lifecycle or injection.
- Use JSON files for substantial, stable wire examples that deserve independent
  review. Do not move every small dictionary into a file. A loader only reads
  fresh data; it must not patch IDs, infer dates, merge defaults or repair input.
- No `FixtureFactory`, `HHScenarioFactory`, `CareerOpsTestDataBuilder`,
  `UniversalPayloadBuilder`, giant kwargs factory or production decision logic
  in test-data setup. Do not generate expected decisions or schema oracles.

## Decisions in CAR-143

- Minimized the synthetic mapper payload and four OBSERVE configuration blocks.
  Mapper identity and asserted mapping fields remain visible. OBSERVE removes
  redundant default values while keeping the same effective configuration,
  explicit identities, query limits, write policy and zero delays.
- Preserved all **16 wire objects** in two fixture catalogues:
  [`raw_v2/audited_apply.json`](../../tests/fixtures/hh/raw_v2/audited_apply.json)
  (10 objects) and
  [`raw_v3/observe.json`](../../tests/fixtures/hh/raw_v3/observe.json) (6 objects).
  Catalogue keys are test labels for complete objects, not a new wire envelope.
  RAW-v3 evaluation sidecars retain their own schema version 1.
- These are **synthetic compatibility examples**, frozen from the independent
  test setup at `955caf2`, not captured HH traffic. The local JSON reader returns
  fresh objects. Object paths and S3 metadata remain explicit in the test module;
  every original serialized body and reference metadata value is preserved.
- Kept missing/malformed TOML and identity mutations visible. Kept RAW purity,
  S3 hash/metadata inputs, exact application evidence and source search pages
  unchanged. Existing small runtime/resume/audit payloads need no new builder.
- Kept legacy filtering, scheduler, stateful ETL doubles and migration machinery
  local. Schema expectations and positional SQL-row oracles are outside CAR-143.
  Historical RAW compatibility remains useful across a future Spark/Scala move.

The audit covered all 24 test modules. At a diagnostic threshold of **15 source
lines per dict/list literal**, there were 39 nodes in 28 outer blocks: 22 payload
blocks and 6 schema/row oracles. Five payload blocks were minimized, eight wire
blocks moved to the catalogues, and nine payload blocks deliberately left as-is.
The result is 26 nodes in 18 outer blocks; three minimized blocks still exceed
the threshold. No size quota, new builders or shared fixture framework was added.

Before accepting fixture changes, compare collected node IDs and assertions,
run FAST and the existing quality checks, and verify body/metadata equivalence
where required. Keep negative inputs visibly negative and prove mutation cannot
leak into another load. CAR-143 preserves cases and oracles; test retirement and
module splitting remain CAR-144/CAR-145 work.
