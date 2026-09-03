# CareerOPS HH multi-account scheduler

The planner reads `/etc/careerops/hh/accounts.toml` and creates one schema-v3
global plan. Enabled accounts are round-robin interleaved, and every worker
launch respects the configured global minimum gap. In `observe`, each account
receives its own `observe_runs_per_day` slots; there is no application quota or
shortfall carry.

Each OBSERVE launch reserves the next query window in PostgreSQL by stable HH
source profile. The default is at most 25 search queries per run; the complete
catalog rotates deterministically across runs and account-key renames do not
reset its cursor.

In `apply`, the planner uses the separate `apply_runs_per_day` cadence and puts
`max_apply_per_run` into every slot. Configuration validation requires
`apply_runs_per_day * max_apply_per_run >= apply_daily_cap`; the committed
`7 * 15` example can therefore reach a cap of `100`.

The dispatcher timer wakes locally every five minutes and launches at most one
due account slot. Its worker command passes only the explicit runtime mode and
account key. It never adds `--live`, a profile, or a static resume ID.

State is account-scoped: pause reason, completed runs, timestamps, errors, and
slot statuses live under each account. A CAPTCHA or reconciliation failure for
one account pauses only that account; later due slots for other accounts remain
serviceable.

Safety defaults:

- `CAREEROPS_HH_MODE=observe`;
- `CAREEROPS_HH_ALLOW_EXTERNAL_WRITES=false`;
- `CAREEROPS_HH_RESUME_REGISTRY=postgres`;
- dynamic new resume IDs are registered as `unassigned` and cannot auto-apply;
- non-published resumes keep their binding but cannot auto-apply;
- missing resume IDs remain as deleted history and cannot start new runs;
- a failed `/resumes/mine` request aborts reconciliation before state is saved;
- OBSERVE requires PostgreSQL query-cursor state even when the resume registry
  is explicitly switched to the JSON dev/bootstrap fallback.

For a future guarded APPLY rollout, set a binding's `auto_apply=true`, choose
`mode=apply`, and independently set the external-write environment flag. The
dispatcher then maintains `apply_daily_cap` independently per account and passes
the remaining daily quota plus the slot's `max_apply_per_run` to the worker. One
account-run reconciles resumes, runs every published active
`auto_apply=true` binding (or one explicitly selected binding), and shares a
conservative employer-write-attempt budget across those resume runs. PostgreSQL
claims prevent concurrent or ambiguous duplicate POSTs. OBSERVE state never
receives application quota fields.


## S3 RAW materialization

Completed schema-v3 OBSERVE batches are materialized independently from
SeaweedFS RAW into PostgreSQL by `careerops-hh-materializer.timer`.

The materializer runs every ten minutes, ignores unfinished batches without
`summary.json`, skips non-OBSERVE/legacy batches, and uses
`careerops.observation_runs.id` as the committed checkpoint. Each pending run
is loaded in its own PostgreSQL transaction. Failed runs remain pending and
are retried by a later materializer invocation without blocking HH discovery.
