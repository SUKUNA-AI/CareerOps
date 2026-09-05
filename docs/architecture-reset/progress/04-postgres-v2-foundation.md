# CareerOPS PostgreSQL v2 Foundation

## Goals

Дата: 2026-09-06. Ветка: `ar/postgres-v2-foundation`, база: `architecture-reset`.
Canonical SQLAlchemy Core metadata: `careerops_storage.v2.metadata`, namespace `careerops_v2`.
Результат — foundation для новой базы: 13 таблиц, clean Alembic baseline и one-time recovery contract.
Production cutover, importer и runtime v2 в эту ветку не входят.

## Architecture Reset rules applied

Владение схемой и Alembic переключено на v2. Старые bootstrap/migration/cutover utilities удалены.
Действующие v1 writers сохраняют прежние imports и обращаются к прежнему namespace `careerops`.
`careerops_storage.legacy.schema` содержит замороженные определения v1;
`careerops_storage.schema` — только совместимый import boundary. Новые consumers используют `careerops_storage.v2`.
Это временная граница для незаменённых consumers, а не второй production deploy или bridge migration.

Private recovery directories, application audit archive, `backups/` и `*.dump` добавлены в `.gitignore`.
Их строки, payloads, credentials и идентификаторы не включены в этот документ или commit.
Предшествующий untracked `01-postgres-live-inventory.md` не относится к этому commit.

## Data ownership model

Категория domain current содержит identity registry, нормализованные внешние объекты и отдельную
CareerOPS configuration. Spark владеет только текущим внешним представлением объектов.
Регистрация аккаунтов/profiles и изменение CareerOPS bindings имеют отдельного административного owner.
Реквизиты доступа хранятся вне PostgreSQL schema и repository.

Категория control содержит работу, leases, текущий результат обработки, кандидатов и состояние откликов.
Writer определяется таблицей; Spark никогда не обновляет control tables или resume policy.
Будущие сервисные DB roles должны реализовать эти границы через table grants. Эта ветка не создаёт
login roles, secrets, GRANT deployment или общую runtime учётную запись.

## Domain current-state

`sources -> accounts -> profiles` задают независимые от numeric IDs внешние ключи.
`account_key` — закреплённый registry key, не сменяемый display name. Переименование локального профиля
не может создавать новый аккаунт и обходить application guards. Разрешение aliases требует явного mapping.

`employers`, `vacancies`, `resumes` имеют `materialization_state`, `observed_at`, `raw_uri`,
`content_hash`, `normalization_version`, `materialization_key`. `current` требует полного provenance;
`identity_only` позволяет восстановить ключи без выдуманного источника; `unavailable` сохраняет tombstone.
Current title/description/normalized resume text нужны readers; оригинальные JSON payloads не хранятся.
`content_hash` — SHA-256 выбранного RAW объекта; `materialization_key` идентифицирует успешный batch.

`resumes` содержит upstream status/lifecycle. `resume_bindings` содержит routing, version, target,
query-set assignments, enabled и auto-apply policy. Один current binding на resume, `auto_apply=false`
по умолчанию. У unassigned resume отсутствует binding; disabled binding сохраняется с `enabled=false`.
Изменения policy повышают `binding_version`; переиспользовать старый version с новым содержимым нельзя.

Selection flags не дублируются: evaluation eligibility выводится из current materialization,
active/present resume и enabled binding. Auto-apply eligibility дополнительно требует published,
`auto_apply=true`, актуального кандидата, application guard и свежего precheck. Восстановленные bindings
не разрешают автоматически начать processing до восстановления current upstream objects.

## Operational/control state

`source_tasks` заменяет fine-grained progress/cursors старого scheduler, `processing_jobs` — старые
evaluation work items. `match_results` и `application_candidates` содержат только current результат пары.
`applications` — authoritative состояние application owner; `application_guards` — сериализующий
duplicate-protection key, не новый журнал попыток. Leases не являются разрешением повторного HH submit.

## Table inventory

| table | category | owner | writer | readers | source of truth | natural key | recoverability | notes |
|---|---|---|---|---|---|---|---|---|
| sources | domain identity | identity registry | identity provisioning | all services | PG registry | source_key | explicit mapping | no credentials |
| accounts | domain identity | identity registry | identity provisioning | Spark resolver, adapter, application owner | PG registry | source_id + account_key | recovery mapping | key frozen across aliases |
| profiles | domain identity | identity registry | identity provisioning | Spark resolver, adapter | PG registry | source_id + profile_key | recovery mapping | composite FK enforces account/source |
| employers | domain current | normalization | Spark merge | processing | RAW/Lake -> current projection | source_id + source_employer_id | replay | optional identity shell |
| vacancies | domain current | normalization | Spark merge | processing, application owner | RAW/Lake -> current projection | source_id + source_vacancy_id | replay | no attempts/history |
| resumes | domain current | normalization | Spark merge | processing, application owner | RAW/Lake -> current projection | account_id + source_resume_id | preserve identity; replay/refetch content | no binding policy |
| resume_bindings | domain configuration | CareerOPS policy | configuration service | adapter, processing, application owner | PG policy | account_id + binding_key; one per resume | explicit recovery; not RAW replay | default auto-apply disabled |
| source_tasks | control | adapter | task producer + adapter workers under one protocol | orchestration, operators | PG queue | account_id + task_key | preserve queue; old queue not imported | persistent defer/retry/lease |
| processing_jobs | control | processing | processing producer/workers | application owner, operators | PG queue + artifact manifest | vacancy + binding + binding_version + input_fingerprint + pipeline_version + policy_version | re-enqueue pinned inputs; retain current references | pair is processing unit |
| match_results | control current | processing | processing result transaction | candidate producer, application owner | PG current decision | vacancy_id + binding_id | recompute | score, reasons, job, artifact URI |
| application_candidates | control current | processing | processing result transaction | application owner | PG current proposal | vacancy_id + binding_id | regenerate | eligible/review/withdrawn, expiry |
| applications | control | application owner | application owner only | processing, reconciliation, operators | PG authoritative outcome + S3 evidence | account_id + idempotency_key; opaque UUID identity | backup + fail-closed audit collapse | final upstream natural identity deferred |
| application_guards | control | application owner | same transaction as applications | application owner | PG suppression gate | account_id + source_vacancy_id | must recover successful/uncertain holds | conservative scope; no TTL |

## Table-by-table rationale

- `sources`: canonical source labels shared by identities and source-scoped domain objects.
- `accounts`: stable authentication scope without any auth material; avoids using resume/profile as account identity.
- `profiles`: preserves the old source/profile mapping while separating its account association.
- `employers`: deduplicated current employer identity; same-source composite FK from vacancy.
- `vacancies`: normalized current fields needed for processing and selection, plus source provenance.
- `resumes`: one upstream identity per account; lifecycle and representation owned by normalization.
- `resume_bindings`: policy survives upstream refresh; no Spark UPSERT can silently enable auto-apply.
- `source_tasks`: one persistent work record per operation/generation/page; children keep their parent/account.
- `processing_jobs`: independently retryable, version-pinned vacancy/binding work with fenced leases.
- `match_results`: replaces current decision for a pair; composite job FK prevents attribution to another pair.
- `application_candidates`: one expiring proposal per pair; application owner does not write processing decisions.
- `applications`: current workflow/outcome, latest evidence references and recovery provenance, not raw attempts.
- `application_guards`: permanent key serializes competing resumes for the same account/vacancy while research is open.

No v2 table corresponds one-to-one to `batch_runs`, `vacancy_decisions`, `observation_runs`,
`vacancy_observations`, `evaluation_work_items` or `observe_query_cursors`.

## Natural keys and idempotency

Domain IDs use `bigint GENERATED BY DEFAULT AS IDENTITY`; worker/application IDs are caller-generated UUIDs.
Numeric IDs are local references, never replay keys. Composite FKs prevent mismatched source/account/profile,
employer/source, resume/account and processing-job/pair references. FK deletes are restrictive, not cascading.

Task key contract: hash a canonical, versioned operation envelope containing source/account, task kind,
observation window or sync generation, normalized request identity and page/cursor/entity identity.
Retries reuse the key. A new scheduled observation has a new generation; otherwise a successful old task
would incorrectly suppress all later refreshes. Same key with different parameters is a conflict, not an update.
`parameters` is the only JSONB: a bounded object (16 KiB) of varying query/page/fetch arguments, never auth,
raw responses, credentials, traces or applicant messages.

Processing fingerprint is SHA-256 of the immutable input manifest: exact vacancy and resume versions/hashes,
binding snapshot and dependency/model versions. Pipeline/policy identities are also explicit columns in the
UNIQUE work key. Identical completed work is reused; retry does not create another historical attempt row.

Application idempotency key identifies a command/recovered compact record, not an HH attempt.
Recovery provenance has a separate UNIQUE `(recovery_source, recovery_record_key)`.
The guard PK is a versioned safety policy (`account_vacancy_v1`), not a declaration that HH natural
application identity is account/vacancy. There is no irreversible UNIQUE resume/vacancy on applications.

## Spark writer boundary

Spark resolves registry IDs and writes only employers/vacancies/resumes current columns.
Use per-batch staging outside the authoritative tables, validate DQ, then merge in FK order with the natural
UNIQUE keys. Temporary staging can be created on a future Spark connection; no persistent staging framework
or Spark implementation is introduced here. Identity shells can be completed by the same UPSERT key.

Batch merge must compare source observation time and pinned normalization version. An older observation must
not overwrite newer state. Same timestamp + same hash/version is a no-op; conflicting equal-time evidence goes
to DQ, not arbitrary last-writer-wins. A normalization-version replay is an explicit controlled refresh.
Set `updated_at` explicitly on a real update; schema defaults apply on insertion only.
Never merge a whole-account resume disappearance from a partial/failed resume sync.
Never delete/reinsert domain rows referenced by processing/applications, or modify bindings/control state.

## Adapter writer boundary

Adapter writes immutable external responses to RAW and manages `source_tasks` only. It does not materialize
PG domain objects. A successful task references a durable completion manifest at `result_artifact_uri`.
Work producer and workers use one adapter-owned queue protocol; Airflow merely requests a generation and reads
coarse completion. Task parameters identify the request; secrets are resolved separately at execution time.

Persist newly discovered child/page/fetch tasks before acknowledging their parent. Commit children and parent
completion atomically. A crash after RAW publication is replayed against the same task/child keys.
Outstanding child tasks are not inferred solely from an in-memory vacancy list or S3 object listing.

## Processing writer boundary

Processing owns jobs, results and candidates. Only claim/update rows with the current lease token and expected
state. Load exact manifest inputs, not whatever current objects happen to exist later. Publish large artifacts
first, then atomically finalize the job and replace current result/candidate when its inputs still match the
current domain and binding version. Stale work may complete as an artifact but cannot overwrite a newer result.
Reject candidate creation unless the completed result is eligible; withdrawn/review decisions invalidate
previous eligible proposals. `expires_at` must be refreshed deliberately with each new current proposal.

The application record pins `processing_job_id` separately because the candidate row is mutable current state.
Readers must compare candidate job, match job, current domain fingerprints and binding version before using it.
Old terminal jobs may be archived after their current result/candidate/application references are gone;
attempt history and traces belong in S3/Lake. No cleanup worker is implemented.

## Application owner boundary

Only application owner may write applications or guards. A transaction creates/resolves a guard, inserts the
idempotent application row and pins the guard to it. The FK cycle is deferred to commit: each application must
have a guard and every guard points to an application for the same account/external vacancy. It supports a
single atomic insert transaction, not an unprotected intermediate commit.

Lock the guard before claiming an application. If its pinned record is successful or uncertain, refuse submit
for every resume in the account. Workers must verify the guard still points to their application, compare the
lease token, and use CAS state transitions. Guard has no expiry and cannot be released by a TTL reaper.
Reassigning its pointer is allowed only after the previous state is proven safely retryable and research/policy
permits the new command. Confirmed submission is never automatically reassigned or downgraded.

The owner must validate candidate -> job -> binding -> resume/account and vacancy source/external identity in
one transaction, recheck eligibility, then perform a fresh upstream precheck. FKs enforce local relationships;
they do not prove freshness or an HH side effect. These transactional protocols, transition authorization and
role grants are requirements for the later owner implementation; this foundation provides no submit API.

## S3/Lake boundary

`careerops-raw`: immutable HH source responses/application audit; `careerops-lake`: normalized/history Parquet;
`careerops-artifacts`: processing inputs, evidence manifests, reranking/LLM/scoring artifacts and control manifests.
PG stores compact state and opaque object URIs, not signed URLs, auth configuration or full payloads.
No full attempt log, trace table, event store, warehouse or ClickHouse schema is introduced.
Objects referenced from live control state must remain available under retention policy.

## Source task lifecycle

`pending -> claimed -> running -> succeeded`.
Claim increments `attempt_count` and sets owner, UUID fencing token, leased_at and expiry.
Due work uses `(account_id, next_attempt_at, id)` partial index and future `FOR UPDATE SKIP LOCKED`.
The expired-lease index supports recovery of crashed workers.

`claimed/running -> deferred` for limits/quotas/throttles; set next_attempt_at and clear lease.
`claimed/running -> retryable_failure` for transient failures, with the same persistent task key.
`terminal_failure` is an explicit unrecoverable error requiring operator disposition; `cancelled` is an explicit
operator decision, never the consequence of a quota or max-vacancies budget. Terminal rows retain evidence.
The CHECK rejects quota/throttle/limit categories in terminal states. On reacquisition clear the stale error
category as part of the same update. No max-attempts rule silently discards deferred work.

CHECKs enforce allowed states, nonnegative attempts, complete leases, retry scheduling, terminal timestamps,
success URI and failure category. They validate row shape, not old-to-new transitions. Expiry alone cannot
authorize an old worker to commit: updates must include token and expected state.

## Processing lifecycle

Same explicit queue shape: `pending -> claimed -> running -> succeeded`, with `deferred`,
`retryable_failure`, `terminal_failure` and explicit cancellation. Claim/reclaim rotates the fencing token.
Ready and expired-lease indexes support worker acquisition and recovery. Full processing policy, deterministic
core, C++, Jina and optional LLM artifacts are referenced by pinned versions/manifests, not implemented here.
Reconciliation of an expired processing lease may schedule the same job again; final publication is idempotent.

## Application lifecycle

`candidate -> preparing -> precheck -> submitting -> submitted_confirmed`.
Alternative outcomes are `submitted_unconfirmed`, `uncertain`, `safe_failure`, `blocked` and
`reconciliation_required`. Default blocked state requires a reason; enabling execution requires explicit
candidate/resume/job references. Preparing/precheck/submitting require a live-shaped lease and positive attempt count.

Commit `submitting` and request/audit evidence reference before the external call. An expired submitting lease
becomes reconciliation work, never an automatic POST. A crash after a possible submit is `uncertain` unless
independent evidence proves no side effect. Confirmation requires its timestamp and upstream evidence URI.
Submitted timestamps mean observed submission evidence time; they do not claim an unavailable exact HH server time.

Unconfirmed/uncertain/reconciliation states require `next_reconcile_at`; confirmed cannot have next_attempt_at.
Only safe_failure may have next_attempt_at, and retry must pass preparing/precheck again. An old precheck cannot
be reused: clear old evidence on claim and record the current one before submitting. Lease expiry is not a
safe-failure proof. Latest audit/precheck/evidence URIs replace large payloads and attempt logs.

## Legacy recovery contract

Contract version: `careerops-v2-recovery/1`. This is a specification, not an importer.
Local inventories/dump/audit are private, read-only recovery inputs. Inventory shape and aggregate facts were
checked locally; edge was not contacted. The requested `03-application-history-recovery.md` is absent in this
checkout, so its text could not be reviewed. The prompt's 189/176/168/4/4 summary is independently consistent
with local application-result files. No replacement copy of private data is committed.

Input manifest (private): contract version, stable recovery source marker, exact input file/object checksums,
original S3 URI mapping, collection timestamp, source/account/profile resolution map and classification policy.
The marker and per-record keys are stable across reruns; generation time is not an idempotency key.
Missing/checksum-conflicting evidence or ambiguous mapping blocks the affected import and activation.

Required mappings and transforms:

1. Preserve `(source, profile_key, account_key, legacy_source_profile_id)` in the private mapping manifest.
   Create/resolve sources by source_key, accounts by source+account_key, profiles by source+profile_key.
   An existing profile mapped to another account is a hard conflict. Never infer accounts from numeric ID order.
   Expected current inventory has three profiles; preserve their exact account mapping, without publishing it.
2. Resolve six resumes by old profile mapping and source_resume_id; assign new numeric IDs and record the
   old -> new ID mapping privately. Detect duplicate account/resume identities before writing. Current lifecycle,
   presence and upstream status may be copied as observed facts, but all six lack raw_uri in the inventory:
   initialize `materialization_state='identity_only'` until new normalization supplies valid RAW provenance.
   Do not invent raw_uri, observed_at, source hash, text or a normalized current snapshot.
3. Recover each binding_key, binding_version, target_key, enabled, auto_apply and query-set assignment from
   exact dump/config evidence. Map query_sets -> query_set_keys. Preserve disabled policy and absence of binding
   if discovered. Six present bindings are enabled, auto_apply=false. Do not default missing binding_version,
   target or query sets silently. Derived selection flags are compared with legacy facts, not imported as authority.
   The new current-materialization gate intentionally keeps identity-only resumes ineligible initially.
4. Preserve 172 successful/unconfirmed protections as applications + guards in atomic transactions.
   Resolve the historical profile/resume through the mapping, not title or array position. Local historical
   profile/resume identities are present in the current inventory. If another archive introduces unresolved
   ownership, stop activation for that source/account; do not create a guessed account or weaken guard scope.
   A missing historical resume may remain nullable on a blocking imported application only after account
   identity is proven; minimum six current resume identities must still be recovered.
5. Application source_vacancy_id comes from audit; no vacancy materialization is needed to install protection.
   `candidate_id` and `processing_job_id` are NULL for historical imports. `attempt_count=0` measures v2 attempts,
   not 189 historical attempts. Legacy outcomes and attempts remain in RAW/Lake.
6. Set imported_from_legacy=true, recovery_source, stable recovery_record_key, imported_at, audit_uri and
   evidence URI where required. Use an immutable collapse manifest per protected record; reference all contributing
   attempts there. recovery_record_key derives from versioned source/account/external-vacancy scope; never run_id
   alone. Idempotency key is namespaced by contract version + recovery source + record key. Reuse the same UUID
   on reruns via this UNIQUE key, or deterministic UUIDv5 with a namespace fixed in the private manifest.
7. Do not import old batch_runs, observations, evaluation items, cursors, planner files or processing runs.
   Do not bulk-import vacancies/employers as operational state; replay domain current via future Spark.

Importer acceptance gate: three resolved profile mappings, six resumes, six preserved policy bindings, 168
confirmed and four unconfirmed protected identities; 172 guards for this inventory. Verify every FK and account
scope, exact policy values, audit reference completeness, no enabled auto-apply and no legacy queue rows.
Rerun must produce no duplicate identities or changed outcomes. Conflicting existing records fail closed.
Reordered retries cannot downgrade confirmed/unconfirmed to failed. Retain the backup and private mapping
manifest until independent recovery verification and the later production cutover approval.

## Application history migration semantics

Raw attempt != application identity. Collapse retries within the verified source/account/external-vacancy
scope for conservative protection, retaining actual resume evidence in the private manifest. This is a recovery
suppression policy, not the unresolved HH same-account/multi-resume identity decision.

| Recovered category | v2 state | Required behavior |
|---|---|---|
| 168 CONFIRMED_SUBMITTED | submitted_confirmed | Never auto retry; persistent guard; confirmation/evidence/audit references |
| 4 SUBMITTED_UNCONFIRMED | submitted_unconfirmed | Block auto retry; persistent guard; next_reconcile_at scheduled |
| 4 FAILED_ONLY | no mandatory operational import | Not submitted; retain attempts in RAW/Lake; new candidate and fresh precheck required |

All 17 raw failures were daily-limit failures according to the supplied recovery summary; a later confirmed
outcome dominates an earlier failure. UNKNOWN/conflicting/unreadable evidence is not FAILED_ONLY: quarantine
it and block activation pending reconciliation. If FAILED_ONLY is explicitly imported later, use safe_failure
with a reason and audit provenance, next_attempt_at=NULL, and never synthesize a candidate or authorize retry.
Use legacy finished_at as the time at which submit/confirmation was observed, record this basis in the manifest;
do not substitute importer time for missing historical evidence. Missing mandatory timestamps require resolution.

## Alembic v2 baseline

Revision `20260906_v2_0001`, down_revision=None, branch label `v2`; one head.
Root `alembic.ini` now selects this clean lineage. Removed 0005 has no bridge, stamp or upgrade path here.
Baseline is frozen explicit Alembic operations; it does not import mutable current metadata.
Migration environment imports canonical v2 metadata only. Version table: `public.alembic_version_v2`.

Offline commands do not read a DSN or open a connection:

```text
python -m alembic heads
python -m alembic history
python -m alembic upgrade head --sql
```

Online commands in a later approved task require the dedicated `CAREEROPS_V2_POSTGRES_DSN` PostgreSQL URL;
there is no fallback to legacy CAREEROPS_POSTGRES_DSN or test DSNs. New target must not contain `careerops`
or `public.alembic_version`; guard checks exist in both online environment and emitted baseline SQL.
Provision a new database with an absent `careerops_v2` namespace. CREATE SCHEMA intentionally rejects even an
unversioned pre-existing empty namespace; no IF NOT EXISTS conceals unexpected state. Normal future upgrades
of an already versioned v2 target remain possible. Existing live v1 version skew is not a compatibility requirement.
Downgrade explicitly removes only v2 objects without CASCADE; it is destructive and was not executed.

Static validation performed: ruff on changed Python files, mypy on 11 affected storage source files with
follow-imports=silent, heads/history, offline SQL generation and inspection, and git diff --check.
In-memory offline comparison confirms frozen baseline columns, types, nullability, defaults, identities and
constraints for 13 tables; 19 secondary indexes and the deferred application guard FK are emitted.
No production connection, online migration, test execution or test/CI edit is part of validation.
Constraint enforcement under concurrent transactions awaits the separate DB/runtime testing workstream.

## Legacy DB code deleted now

| Deleted path | Evidence / reason |
|---|---|
| src/careerops_storage/alembic_cutover.py | Only cutover CLI and old tests import it; destructive disposable reset/stamp/repair proof has no runtime caller |
| scripts/validate_alembic_cutover.py | Sole CLI consumer of that obsolete framework; replaced foundation requires no legacy cutover |
| alembic/versions/20260904_0005_current_schema_baseline.py | V1 lineage replaced by the independent v2 baseline |
| sql/migrations/0001_create_oltp_core.sql | V1 schema bootstrap; no runtime consumer |
| sql/migrations/0002_add_oltp_indexes.sql | V1 index bootstrap; no runtime consumer |
| sql/migrations/0003_add_hh_application_claims.sql | V1 claims bootstrap; no runtime consumer |
| sql/migrations/0004_add_hh_orchestration_state.sql | V1 observe/evaluation bootstrap; no runtime consumer |
| sql/migrations/0005_repair_legacy_oltp_schema.sql | Obsolete schema compatibility repair; no runtime consumer |

Caller search covered src, scripts, infra and tests. Existing integration/unit tests still refer to deleted
bootstrap artifacts; those are explicitly obsolete bootstrap checks, not production consumers. Historical
versions remain in Git history; this branch does not provide fresh deployment tooling for v1.

## Legacy DB code intentionally kept until replacement

- `postgres.py` and legacy exports: used by application_cli/batch_cli and both materialization scripts.
  Remove relevant writers with adapter, Spark and application-owner replacement; then remove legacy schema shim.
- `careerops_integrations/hh/application_claims.py`: active contracts used by postgres and application_audit.
  The requested `careerops_storage/application_claims.py` does not exist; its implementation lives in postgres.py.
  Keep until ar/application-owner replaces actual submission/claim consumers.
- `careerops_integrations/hh/application_audit.py`: current guarded submit/audit consumer; replace with owner.
- `careerops_etl/hh_s3_to_postgres.py`, backfill_hh_postgres.py and materialize_hh_pending.py:
  active Python materialization path; replace in ar/spark-data-pipeline before deletion.
- Planner/dispatcher, systemd services and HH adapter runtime: their replacements are future branches.
- `s3.py`, contracts and shared connection plumbing: keep; reassess the connection helper after the last v1 writer.

## Known compatibility breakages

Fresh v1 SQL bootstrap and old Alembic cutover commands no longer exist. Existing runtime Python import paths
remain available; no v1 SQL statement was rewritten to target v2 and no production ownership was switched.

Obsolete tests identified, unchanged for the separate tests/CI workstream:

- test_alembic_cutover.py, test_alembic_migrations.py, test_alembic_postgres_integration.py import removed helpers;
  their legacy cutover scenarios and collection must be retired/replaced in that workstream.
- test_alembic_baseline.py asserts old revision, DSN behavior and v1 DDL; it is obsolete for canonical Alembic.
- test_postgres_integration.py fixture bootstraps deleted sql/migrations 0001–0005.
- test_postgres_storage.py::test_application_claim_migration_uses_canonical_oltp_ids and
  ::test_insert_columns_are_declared_by_ordered_migrations assert the deleted bootstrap files.

Legacy storage-schema checks remain associated with the compatibility definitions, not v2 coverage.
No claim of a green full test suite is made. The branch contains these intentional test-workstream dependencies.

## Deferred decisions

- HH same-account/multi-resume application semantics and final natural application identity; retain broad guards.
- Exact worker claim/transition SQL, lease duration, backoff policy, privileges and concurrent-operation tests.
- External-account aliases and formal upstream identity resolution; registry keys cannot be silently reassigned.
- Spark stage/merge implementation, normalization policies and replay version precedence.
- Recovery importer/manifest tooling, evidence reconciliation and independently verified production activation.
- Source parameter contracts per task kind, artifact retention and terminal-job archival schedules.
- Processing implementations, model/policy versions, scoring thresholds and candidate TTL values.

## Explicitly out of scope

HH adapter implementation, RAW redesign, Airflow deployment/DAGs, Spark/Python-materializer replacement,
C++, Jina, LLM, processing workers, application importer or submit implementation, ClickHouse/OLAP,
production deployment/reset, edge changes, final repository cleanup, tests and CI.

## Next Architecture Reset branch

Merge this foundation into `architecture-reset` after review and coordination with the tests workstream.
The next planned implementation block is `ar/hh-adapter-raw`; it must consume source-task/RAW boundaries and
replace its legacy consumers before deleting them. No next branch is started by this task.
