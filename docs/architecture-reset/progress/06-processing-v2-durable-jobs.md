# Processing v2 durable jobs (P2-02)

Status: permanent queue/lifecycle foundation for `ar/processing-v2`.

## Scope

P2-02 turns the existing `careerops_v2.processing_jobs` table into the durable work queue
owned by the standalone `careerops-processing` service. It does not implement filtering,
requirements/evidence, Jina calls, scoring, Spark materialization or APPLY.

The stage adds:

- pure Processing queue contracts;
- PostgreSQL queue implementation;
- desired-work reconciliation and explicit pair withdrawal;
- `SKIP LOCKED` claiming and expired-lease reclaim;
- UUID fencing tokens;
- lease renewal/heartbeat;
- deferred/retryable/terminal/cancelled transitions;
- artifact-first success acknowledgement;
- worker lifecycle used by later Processing executors.

## Work identity

One durable work identity is the existing PostgreSQL uniqueness tuple:

```text
vacancy_id
+ binding_id
+ binding_version
+ input_fingerprint
+ pipeline_version
+ policy_version
```

`input_fingerprint` is produced by the immutable P2-01 `ProcessingInputManifest`. Job IDs,
claim timestamps, leases and result artifact URIs do not participate in semantic work
identity.

Exact successful or terminal work is not silently recomputed. A new semantic input,
pipeline version or policy version produces different work. Exact work cancelled only because
it was `superseded` may be reactivated if authoritative current state later returns to that
same immutable input. Operator/manual cancellation is deliberately not reactivated by normal
reconciliation.

## Reconciliation

Reconciliation is explicit and idempotent.

For a desired pair:

```text
exact work exists -> reuse exact job identity
exact superseded work -> reactivate as pending
new exact work -> insert pending job
other active work for same pair -> cancelled/superseded
```

For a pair that leaves desired state, the producer must issue an explicit withdrawal:

```text
withdraw_pair(vacancy_id, binding_id)
```

That transition cancels pending/claimed/running/deferred/retryable work and clears its lease,
so an inflight stale worker is fenced immediately. Absence from one partial/keyset page is
never interpreted as withdrawal; the caller must derive withdrawals from an authoritative
current-state reconciliation.

Pair reconciliation and withdrawal use a short PostgreSQL transaction advisory lock to
serialize competing desired-state writers. No advisory lock is held during S3, Jina, matching
or worker execution.

## Claim and fencing

Workers claim one due job with `FOR UPDATE SKIP LOCKED`. Claiming:

- admits `pending`, due `deferred` and due `retryable_failure` work;
- reclaims expired `claimed`/`running` work;
- increments `attempt_count`;
- creates a fresh UUID `lease_token`;
- records owner and lease timestamps.

Every mutation after claim requires:

```text
job id
+ exact lease_token
+ claimed/running state
+ unexpired lease
```

A superseded, withdrawn, expired or reclaimed worker therefore receives
`ProcessingJobLeaseLost` instead of publishing stale success.

## Heartbeat

`ProcessingWorker` moves a claim to `running`, runs the later business executor and renews the
lease every one third of the configured lease duration. If renewal loses the fence, the
inflight executor is cancelled and its output is never acknowledged.

Expected lease loss is not process-fatal in `run_forever`; the worker continues to other
durable work. Unexpected infrastructure failures still surface so the service/container can
restart rather than silently corrupt queue state.

## Failure semantics

Business/technical execution returns one operational disposition:

```text
SUCCEEDED
DEFERRED
RETRYABLE_FAILURE
TERMINAL_FAILURE
```

These are queue states, not matching outcomes. `SKIP`, `REVIEW` and
`APPLICATION_CANDIDATE` remain later semantic results inside a successful Processing job.

An unhandled executor exception becomes `retryable_failure` with a bounded retry delay.
Jina/model-specific executors introduced later can return `deferred` for unavailable
capacity/runtime without converting infrastructure failure into a low match score.

## Artifact-first acknowledgement

A worker may acknowledge `succeeded` only after its executor has already produced an
`s3://` result artifact URI. PostgreSQL success then stores that URI and clears the lease.

If artifact creation succeeds but the final PostgreSQL transition fails, the object may be an
orphan and can be garbage-collected later. PostgreSQL must never record `succeeded` without
an immutable result artifact reference.

## Separation from current match publication

P2-02 owns durable computation jobs only. It intentionally does not update `match_results` or
`application_candidates`.

A previously successful exact job may become current again without recomputation. P2-07 must
therefore reconcile current match/candidate publication from the pinned successful job and its
immutable result artifact; queue re-execution is not used as a substitute for current-state
publication correctness.

## Spark dependency

None of P2-02 depends on Spark implementation. Live desired-work production remains blocked
until normalized current refs satisfy the P2-01 contract, but queue/reconciliation/worker
semantics are final and testable against contract fixtures and PostgreSQL v2.

## Definition of Done

- no second processing queue/schema exists;
- work identity is deterministic and idempotent;
- newer desired work fences older active work for the pair;
- explicit pair withdrawal fences stale active work;
- only `superseded` exact jobs are automatically reactivated;
- due work is claimed with `SKIP LOCKED`;
- expired leases are reclaimable with a new fencing token;
- stale tokens cannot renew, finish, retry or cancel a reclaimed job;
- long-running executors are heartbeat-protected;
- deferred/retryable work is preserved rather than sampled/dropped;
- success requires an S3 artifact URI;
- unit tests cover reconciliation and worker lifecycle;
- PostgreSQL integration tests cover idempotency, supersession, withdrawal, lease reclaim,
  deferred scheduling and artifact-first completion.
