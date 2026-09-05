# CAR-146 — Test suite metrics and flaky-test policy

Feature: **CAR-139 — Test suite architecture and cleanup**.
Defined on **2026-09-06** after CAR-140–CAR-145 cleanup.

## 1. Purpose

CAR-146 establishes a reproducible way to measure the CareerOPS test suite and a
policy for handling flaky tests without hiding failures.

The goals are to:

- keep test counts, duration, and coverage as measured engineering signals;
- distinguish FAST coverage from PostgreSQL integration coverage;
- make regressions visible without turning arbitrary percentages into vanity
  targets;
- prevent retries, skips, or xfail markers from masking nondeterministic tests.

This document does not introduce a coverage merge gate. A coverage threshold may
be added later only after a stable baseline and a clear ownership policy exist.

## 2. Current post-cleanup baseline

Measured locally on 2026-09-06 from commit `1a55c7c` on branch
`CAR-139-test-suite-architecture-cleanup`, using the worktree-local Python
3.13.13 virtual environment on Windows.

| Measurement | Value | Evidence |
|---|---:|---|
| Total collected cases | 213 | Main `tests/` suite |
| FAST cases | 204 | Passed with `-m "not integration_postgres"` |
| PostgreSQL integration cases | 9 | Collected, not executed |
| FAST duration runs | 2.18 s / 2.11 s / 2.13 s | Three consecutive warm local runs |
| FAST duration median | 2.13 s | Median of the three runs |
| FAST coverage run duration | 3.35 s | Same FAST selection with branch coverage enabled |
| Statements | 3,858 total / 913 missing | `pytest-cov` FAST report |
| Statement coverage | 76.3% | 2,945 of 3,858 statements executed |
| Branches | 1,032 total / 275 partial | `pytest-cov --cov-branch` FAST report |
| Combined branch-enabled coverage | 72% | Coverage.py `Cover` total |
| Ruff | PASS | `src scripts tests` |
| mypy | PASS | 36 source files |
| `git diff --check` | PASS | No whitespace errors |

These values are a snapshot, not quotas. CAR-144 intentionally reduced the suite
from 219 to 213 cases by removing proven redundant tests while retaining their
substantive assertions. CAR-145 reorganized HH tests without changing the case
count.

PostgreSQL collection is not PostgreSQL execution. Integration runtime and
coverage remain unmeasured until the dedicated CI path is implemented safely.

## 3. Reproducible FAST duration measurement

Use the worktree-local virtual environment and measure the same selection used by
FAST CI:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider -m "not integration_postgres"
```

For a duration baseline, run the command three consecutive times in the same
checkout and environment. Record all three elapsed times and use the median as
the comparison value. Do not treat a single run, especially immediately after
installation or cache invalidation, as a performance regression.

Duration comparisons are meaningful only when the following are also recorded:

- commit SHA;
- Python version;
- OS / runner type;
- selected test boundary;
- whether the environment was freshly installed or already warm.

No hard FAST duration gate is defined by CAR-146. A future gate must use repeated
CI evidence and a threshold large enough to avoid normal runner noise.

## 4. Coverage measurement

`pytest-cov` is a development dependency so coverage can be measured with the
same pytest selection as the FAST suite.

Canonical FAST coverage command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider -m "not integration_postgres" --cov=src --cov-branch --cov-report=term-missing --cov-report=json:reports/coverage-fast.json
```

Coverage produced by this command is **FAST coverage only**. It must not be
reported as complete system coverage because the 9 real PostgreSQL cases are not
executed by this selection and there is currently no real S3 or E2E boundary.

The first post-cleanup FAST measurement on commit `1a55c7c` produced:

- 3,858 statements total;
- 2,945 statements executed and 913 missing, or 76.3% statement coverage;
- 1,032 branches with 275 partial branches reported;
- 72% total branch-enabled Coverage.py `Cover` value;
- 204 FAST cases passed and 9 PostgreSQL cases deselected;
- 3.35 s elapsed time with coverage instrumentation.

The terminal summary does not expose a separate standalone branch-coverage
percentage, so the baseline records the branch counts and Coverage.py's combined
branch-enabled `Cover` value rather than inventing a derived branch percentage.

Record at minimum:

- statement coverage percentage;
- branch counts and branch-enabled total coverage when reported;
- covered / missing statements;
- commit SHA and Python version;
- the exact pytest marker selection.

The JSON report is an artifact for inspection, not a tracked source file. Keep
`reports/` out of commits.

CAR-146 deliberately does not set `--cov-fail-under`. A threshold should be added
only after the measured baseline is stable and gaps are classified by behavior.
Coverage must not become a reason to add tests that assert implementation detail
or duplicate existing behavior.

## 5. What coverage can and cannot prove

Coverage answers whether code was executed; it does not prove that behavior was
asserted correctly.

Prefer missing-behavior analysis from CAR-140 over chasing percentages. A line
covered only by a weak fake or by an assertion-free path may still represent a
real gap. Conversely, intentionally defensive or platform-specific code can be
legitimate uncovered code.

The following must remain explicit in reports:

- FAST coverage excludes real PostgreSQL behavior;
- fake-backed SQL tests do not establish PostgreSQL semantics;
- fake S3 tests do not establish SeaweedFS/S3 compatibility;
- vendored `hh-applicant-tool/tests/` is outside CareerOPS coverage accounting;
- future integration boundaries must report their coverage separately before any
  combined metric is considered.

## 6. Flaky-test definition

A test is flaky when the same commit and intended environment can both pass and
fail without a corresponding source/configuration change and without an expected
external dependency state change.

Examples include:

- order-dependent shared mutable state;
- race-sensitive timing assertions;
- nondeterministic random data without a controlled seed;
- leaked filesystem/database state;
- reliance on wall-clock boundaries;
- test isolation failures;
- CI-only failures that disappear on rerun without an identified environmental
  cause.

A reproducible deterministic failure is not flaky. A failure caused by an
explicitly unavailable integration service is an infrastructure/setup failure,
not automatically a flaky test.

## 7. Flaky-test handling policy

Mandatory CI must fail closed.

1. **Do not add automatic retries to mandatory test jobs.** A green rerun must not
   replace the original failure signal.
2. **Do not hide flakes with `skip`, `xfail`, broad exception handling, or relaxed
   assertions.** Those mechanisms require a semantic reason independent of
   flakiness.
3. When a suspected flake appears, record the failing node id, commit SHA,
   environment, traceback, and whether a same-commit rerun passed.
4. Reproduce by running the narrowest affected test repeatedly and then the
   relevant owning module/suite to detect order dependence.
5. Fix the root cause: isolate mutable state, use deterministic clocks/seeds,
   remove timing guesses, or repair lifecycle/cleanup boundaries.
6. If a temporary quarantine is unavoidable, it requires an explicit tracked
   issue, owner, rationale, and removal condition. Safety-critical application,
   claim, migration, transaction, and data-integrity tests must not be silently
   quarantined.

A flaky mandatory test is treated as a product-quality problem because it makes
CI trust ambiguous.

## 8. Reproduction commands for suspected flakes

Single case repeated manually:

```powershell
1..20 | ForEach-Object { .\.venv\Scripts\python.exe -m pytest <node-id> -q -p no:cacheprovider }
```

Owning module:

```powershell
.\.venv\Scripts\python.exe -m pytest <module-path> -q -p no:cacheprovider
```

Full FAST boundary:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider -m "not integration_postgres"
```

If order dependence is suspected, reproduce with the exact order from the
original failing run before changing test structure.

## 9. Regression policy

A future change should be investigated when it causes one of the following:

- a new unexplained flaky failure;
- material FAST duration growth across repeated comparable runs;
- coverage loss in a changed behavioral area;
- disappearance of a test boundary without an intentional task;
- a test count change that cannot be explained by added/removed behavior.

No fixed test-count, coverage-percentage, or duration quota is established here.
The first response to a metric change is to explain the behavior change, not to
restore the old number mechanically.

## 10. CAR-146 completion evidence

CAR-146 is complete with the following evidence recorded on 2026-09-06:

- 213 total collected cases;
- 204 FAST cases passing;
- 9 PostgreSQL integration cases collected but not executed;
- FAST duration runs of 2.18 s, 2.11 s, and 2.13 s, median 2.13 s;
- FAST branch-enabled coverage: 3,858 statements, 913 missing, 76.3% statement
  coverage, 1,032 branches, 275 partial branches, 72% combined `Cover`;
- coverage-instrumented FAST run: 204 passed, 9 deselected in 3.35 s;
- Ruff passed;
- mypy passed for 36 source files;
- `git diff --check` passed.

Actual PostgreSQL integration execution remains a later, separately gated CI
responsibility.
