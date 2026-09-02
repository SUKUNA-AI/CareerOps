# CareerOPS HH worker

The shared worker image runs one configured HH account at a time. Authentication
remains owned by the existing `hh-applicant-tool/config/<profile>` layout; there
is no client/session copy per resume.

The default `observe` pipeline is read-only toward HH:

1. Reconcile the account's authoritative `GET /resumes/mine` inventory.
2. Keep existing resume identity/bindings, register unknown IDs as unassigned,
   retain missing IDs as deleted history, and persist publication/lifecycle in
   PostgreSQL OLTP.
3. Atomically reserve the next deterministic PostgreSQL query window for the
   stable source profile and run at most 50 enabled broad queries. The complete
   catalog remains configured and rotates across runs.
4. Persist exact query pages, deterministic search items, full vacancies, and
   CareerOPS provenance sidecars under S3 RAW schema v3.
5. Persist one routing-only audit record for every full-fetched vacancy and
   every active assigned resume. Query overlap is evidence, never a gate; the
   record is explicitly `pending_filtering_v2`, not a relevance decision.
6. Produce `submitted=0`, `confirmed=0`, and
   `external_writes_attempted=0`.

`apply` remains compatibility-oriented and is possible only when both
`--mode apply` and `CAREEROPS_HH_ALLOW_EXTERNAL_WRITES=true` are present. A
dynamic resume also needs an active explicit binding with `auto_apply=true`.
The resume must currently have HH publication status `published`. Before every
POST the worker resolves `source_profile + source_resume_id + vacancy_id` to
OLTP IDs and acquires a PostgreSQL claim unique by `resume_id + vacancy_id`.
`account_key` is provenance only, so renaming it cannot open a duplicate POST.
`SUBMITTING`, `SUBMITTED`, and `UNCERTAIN` claims cannot be retried blindly.
An account APPLY run can orchestrate multiple active bindings through the same
upstream profile/session; its shared run budget is clamped by the scheduler's
remaining per-account daily quota.

Runtime mounts:

- HH profile state: `/app/hh-applicant-tool/config` (read/write for upstream
  token/cookie lifecycle);
- account bindings: `/etc/careerops/hh/accounts.toml` (read-only);
- discovery catalog: `/srv/careerops/app/config` (read-only).

`CAREEROPS_POSTGRES_DSN` must be provided by `/etc/careerops/hh/env`.
`CAREEROPS_HH_RESUME_REGISTRY=postgres` is the production default. The JSON
registry exists only as an explicit `--resume-registry json` development or
bootstrap fallback and is not mounted by the production Compose service. Query
rotation state always remains in PostgreSQL, including with that resume-only
fallback.
