# CareerOPS

CareerOPS — self-hosted платформа для сбора вакансий, хранения исходных данных, нормализации и воспроизводимого сопоставления вакансий с резюме

Проект одновременно решает прикладную задачу поиска работы и используется как Data Engineering / Backend / ML Systems проект

## Нормативная архитектура

```text
HH
│
│ read-only adapter
▼
SeaweedFS S3
Immutable RAW
│
│ Spark normalization
▼
NormalizedVacancy / NormalizedResume
│
├──────────────► SeaweedFS S3
│                normalized bundles
│
└──────────────► PostgreSQL careerops_v2
                 current refs + control state
                          │
                          ▼
                    Processing v2
                          │
                     P2-03 filter
                          │
                    KEEP / EXCLUDE_PROVEN
                          │
                          ▼
              P2-04 Requirements + Evidence
                          │
                          ▼
              P2-05 Jina evidence selector
                          │
                          ▼
              P2-06 deterministic qualification
                          │
                          ▼
              P2-07 scoring / policy decision
                          │
                          ▼
             SKIP / REVIEW / APPLICATION_CANDIDATE
                          │
                          ▼
               будущий Application Owner
```

Spark владеет преобразованием RAW → normalized data

Processing не выполняет нормализацию и не меняет Spark contracts

## Состояние реализации

Реализованы:

- read-only HH ingestion
- immutable RAW в SeaweedFS S3
- PostgreSQL schema `careerops_v2`
- durable source tasks и watermarks
- immutable Processing contracts
- `ProcessingInputManifest`
- durable Processing queue с fencing lease
- P2-03 deterministic high-recall filter
- P2-04 `RequirementSet`
- P2-04 `ResumeEvidenceSet`
- постоянное переиспользование P2-04 semantic artifacts через PostgreSQL registry
- P2-05 `EvidenceCandidateSet`
- P2-05 `P205ResultArtifact`
- отдельный HTTP boundary к Jina reranker runtime
- отдельный GPU service `careerops-reranker`
- runtime identity handshake для model/code/tokenizer/torch/transformers
- P2-06 `RequirementQualificationSet`
- P2-06 deterministic qualification с `MATCHED / NOT_EVIDENCED / UNKNOWN / CONTRADICTED`
- P2-07 bounded scoring и `SKIP / REVIEW / APPLICATION_CANDIDATE`
- fenced PostgreSQL current publication в `match_results` и `application_candidates`
- stale-output invalidation при supersede/reconciliation/withdraw
- content-addressed Processing artifacts в S3
- production wiring `careerops-processing serve` через P2-07
- C++20 `careerops-matching-core` с gRPC control plane и health/capability contract

Пока не реализован Application Owner, который будет владеть финальной отправкой отклика и внешними application guards

P2-07 runtime/contracts реализованы, но production calibration намеренно остаётся `calibration-unset` до Gold Set. Поэтому автоматический `APPLICATION_CANDIDATE` пока является закрытым release gate, а не выдуманным набором thresholds/weights

`careerops-matching-core` пока не публикует фиктивный evaluator: capability API явно сообщает, что evaluate methods недоступны. Текущая P2-06 qualification реализована как deterministic Processing core и не зависит от незавершённого C++ evaluator

## Главные инварианты

### RAW неизменяем

Ответ источника сначала сохраняется в SeaweedFS S3 без смыслового преобразования

Каждое наблюдение имеет точный RAW URI, SHA-256 и `observed_at`

PostgreSQL не заменяет RAW-слой

### Spark владеет normalized data

```text
RAW
↓
parse
↓
normalize
↓
canonicalize
↓
DQ
↓
dedup
↓
NormalizedVacancy / NormalizedResume
```

Spark записывает неизменяемые normalized bundles в S3 и текущую проекцию в PostgreSQL

Processing получает normalized contracts как внешний вход и не дублирует ETL

### Processing работает только с зафиксированными входами

Один job получает точный `ProcessingInputManifest`

Manifest фиксирует:

- `NormalizedRef` вакансии
- `NormalizedRef` резюме
- `BindingSnapshot`
- `TargetPolicy`
- `ProcessingVersionBundle`
- `as_of`

Из manifest вычисляется детерминированный `input_fingerprint`

Processing не должен читать произвольное текущее состояние после начала job

### Единица решения — vacancy × binding

Одна вакансия независимо рассматривается для каждого подходящего binding

```text
vacancy A × resume 1
vacancy A × resume 2
vacancy A × resume 3
```

Каждая пара имеет собственный durable Processing job

### P2-03 исключает только доказанную несовместимость

P2-03 возвращает только:

```text
KEEP
EXCLUDE_PROVEN
```

Неопределённость остаётся `KEEP`

### P2-04 не принимает match decision

P2-04 строит:

```text
NormalizedVacancy → RequirementSet
NormalizedResume  → ResumeEvidenceSet
```

`RequirementSet` сохраняет логическую структуру `ALL / ANY / CONDITIONAL`, importance, modality, polarity, activity, context, threshold и provenance

`ResumeEvidenceSet` сохраняет subject, activity, actor scope, context, polarity, strength, time span и provenance

P2-04 не выбирает evidence для конкретного requirement и не решает, подходит ли кандидат

### Один semantic artifact на одну semantic version

`RequirementSet` не пересчитывается для каждого resume

`ResumeEvidenceSet` не пересчитывается для каждой vacancy

PostgreSQL таблица `careerops_v2.processing_semantic_artifacts` хранит semantic identity → `ProcessingArtifactRef`

Перед первой сборкой используется PostgreSQL advisory lock

Сами semantic artifacts остаются immutable и content-addressed в S3

### P2-05 — selector, а не judge

Для каждого requirement P2-05 детерминированно рендерит query и полный high-recall pool `ResumeEvidence`, затем вызывает отдельный Jina runtime

P2-05 публикует только ordered evidence candidates:

```text
requirement_id
→ evidence_id + rank + relevance_score
```

Низкий relevance score сам по себе не означает несовместимость

Jina не определяет:

```text
MATCHED
NOT_EVIDENCED
UNKNOWN
CONTRADICTED
SKIP
REVIEW
APPLICATION_CANDIDATE
```

Первые четыре состояния принадлежат P2-06, последние три — P2-07

### P2-05 не использует lexical prefilter перед Jina

Candidate pool сохраняет высокий recall: обычный requirement получает весь `ResumeEvidenceSet`

`NOT_REQUIRED` requirement не отправляется в reranker

Пустой `ResumeEvidenceSet` фиксируется как `NO_EVIDENCE` без model call

### P2-06 квалифицирует evidence детерминированно

P2-06 использует только ranked candidates P2-05 и квалифицирует их по semantic identity, polarity, actor scope, evidence strength и time spans

Evidence вне выбранного Jina top-k не может молча создать `MATCHED` или `CONTRADICTED` в обход P2-05

Отрицательный вывод `NOT_EVIDENCED` требует exhaustive evidence check: pre-Jina pool должен покрывать весь `ResumeEvidenceSet`, а selected candidates должны исчерпывать этот pool. Если `top_k < |pool|`, отсутствие decisive evidence остаётся `UNKNOWN`; то же правило применяется к выводу о недостаточной длительности опыта

Jina relevance score не используется как threshold для qualification

```text
MATCHED        → [1,1]
CONTRADICTED   → [0,0]
NOT_EVIDENCED  → [0,1]
UNKNOWN        → [0,1]
```

`ALL` агрегируется через `min`, `ANY` через `max`. Неустранимый `CONDITIONAL` остаётся неопределённым, а недостаточная evidence duration не превращается в contradiction

### P2-07 принимает bounded policy decision

P2-07 использует qualification bounds и first-class structured compatibility components, включая role, seniority, work format и location

`MANDATORY` и hard critical gate не смешиваются. Contradiction обычного `REQUIRED/MANDATORY` requirement влияет на `mandatory_coverage` и после calibration оценивается через versioned `mandatory_min_support`; до calibration такой случай остаётся `REVIEW`

Доказанное нарушение `PROHIBITED` requirement является non-compensable hard gate и приводит к `SKIP`

Если calibrated policy задаёт положительный вес сигналу, который для exact input нельзя построить, этот компонент остаётся `[0,100]`. Его вес не перераспределяется на доступные компоненты, поэтому отсутствие сигнала не может искусственно повысить conservative score

До появления versioned calibration:

```text
calibration-unset
→ APPLICATION_CANDIDATE запрещён
→ violation PROHIBITED может дать SKIP
→ остальные случаи REVIEW
```

После calibration candidate выдаётся только если conservative lower bounds проходят policy. Если даже upper bounds не проходят policy, результат `SKIP`; иначе `REVIEW`

### Runtime Jina полностью pin'ится

Новый P2-05 manifest содержит `JinaVersionBundle`:

- model id/revision
- custom code revision
- tokenizer revision
- backend
- dtype/quantization
- фактическую версию torch
- фактическую версию transformers
- rendering version
- selection version
- block protocol
- token budget
- top-k

`careerops-reranker` не доверяет env для версий torch/transformers: они читаются из реально установленных Python distributions

Каждый `/v1/rerank` request содержит expected runtime identity из manifest. Несовпадение runtime identity является deterministic protocol failure

### Manifest cutover P2-04 → P2-05 явный

Исторические P2-04 manifests без `JinaVersionBundle` остаются валидными для P2-04 replay

P2-05 не выполняется для manifest без pinned Jina identity

Новые P2-05 manifests должны содержать `JinaVersionBundle` до запуска P2-03. Поэтому identity остаётся частью input fingerprint независимо от результата filter

### Ошибки модели и отсутствие evidence не смешиваются

Временная недоступность reranker:

```text
DEFERRED
→ retry later
```

Она не превращается в `NO_EVIDENCE` или отрицательный match

Нарушение runtime identity, response contract или pinned token budget является deterministic failure

Deterministic content-addressed integrity/configuration failures в stage executors завершаются terminal failure; transport failures остаются retryable, а потеря lease продолжает работать как отдельный fencing signal

### Current output защищён fencing

P2-07 сначала публикует immutable artifacts и только потом обновляет PostgreSQL current projection

Current publication требует действующую lease точной job identity. Supersede, reconciliation cancellation и explicit pair withdrawal инвалидируют старый `match_results` и withdraw соответствующий `application_candidates`

Stale worker не может вернуть устаревший result в current state

## Production runtime Processing

```bash
python -m careerops_processing check-config
python -m careerops_processing serve
```

`serve` собирает:

```text
PostgreSQL queue connection
        │
        └── PostgresProcessingJobStore
                │
                ▼
        ProcessingWorker
                │
                ▼
          P207Executor
          │
          ├── P206Executor
          │   └── P205Executor
          │       └── P204Executor
          │           ├── S3ProcessingInputLoader
          │           ├── P2-03 filter
          │           └── P204SemanticArtifactResolver
          │
          ├── ProcessingArtifactLoader / Publisher
          ├── HttpJinaRerankerClient
          │         │
          │         ▼
          │  careerops-reranker
          │         │
          │       Jina GPU
          │
          └── PostgresMatchPublicationStore
```

Queue, semantic registry и fenced current publication используют отдельные PostgreSQL connections, чтобы heartbeat lease не блокировался extraction, S3, model calls или current-result transaction

Health endpoints:

```text
/healthz  liveness процесса
/readyz   готовность полностью собранного Processing worker
```

Processing readiness означает, что зависимости и worker собраны. Временный model outage обрабатывается на уровне job как `DEFERRED`

## Отдельный GPU runtime

`careerops-reranker` находится в `src/careerops_reranker/`

PyTorch/Transformers не входят в обязательные зависимости основного Processing процесса. Для GPU runtime используется optional extra `reranker-runtime` и отдельный container из `infra/compose/reranker/`

Текущий проверенный container profile использует PyTorch 2.14, CUDA 13.2, Transformers 4.57.3 и FP16. Model/code/tokenizer revision pin'ится отдельно через runtime env

Reranker endpoints:

```text
/healthz
/readyz
/v1/rerank
```

`/readyz` публикует фактическую runtime identity

Triton JIT cache хранится отдельно от hardened `noexec /tmp` через `TRITON_CACHE_DIR`, чтобы скомпилированные runtime modules могли загружаться с executable persistent filesystem

Подробный deployment runbook находится в `infra/compose/reranker/README.md`

## Processing runtime environment

Processing использует:

```text
CAREEROPS_PROCESSING_POSTGRES_DSN
CAREEROPS_PROCESSING_S3_ENDPOINT_URL
CAREEROPS_PROCESSING_S3_ACCESS_KEY
CAREEROPS_PROCESSING_S3_SECRET_KEY
CAREEROPS_PROCESSING_S3_REGION
CAREEROPS_PROCESSING_NORMALIZED_BUCKET
CAREEROPS_PROCESSING_ARTIFACTS_BUCKET
CAREEROPS_PROCESSING_ARTIFACTS_PREFIX
CAREEROPS_PROCESSING_RERANKER_URL
CAREEROPS_PROCESSING_RERANKER_TIMEOUT_SECONDS
CAREEROPS_PROCESSING_RERANKER_UNAVAILABLE_DELAY_SECONDS
CAREEROPS_PROCESSING_WORKER_ID
CAREEROPS_PROCESSING_WORKER_LEASE_SECONDS
CAREEROPS_PROCESSING_WORKER_IDLE_SLEEP_SECONDS
CAREEROPS_PROCESSING_HEALTH_HOST
CAREEROPS_PROCESSING_HEALTH_PORT
```

Текущая P2-06 qualification не требует matching-core target. C++ evaluator должен подключаться только после появления versioned evaluate capability, без изменения semantic contracts P2-06/P2-07

Примеры находятся в `.env.example`, `infra/compose/processing/env.example` и `infra/compose/reranker/env.example`

## Структура кода

```text
src/
├── careerops_adapter/
│   └── hh/
├── careerops_integrations/
│   └── hh/
├── careerops_processing/
│   ├── contracts/
│   ├── core/
│   ├── evaluation/
│   ├── infrastructure/
│   ├── service/
│   ├── executor.py
│   ├── queue.py
│   ├── reconciliation.py
│   ├── selector.py
│   ├── semantic_cache.py
│   └── worker.py
├── careerops_reranker/
│   ├── config.py
│   ├── runtime.py
│   └── server.py
└── careerops_storage/
    ├── s3.py
    └── v2/
```

### `careerops_adapter.hh`

Владеет read-only HH ingestion и не выполняет matching или отправку откликов

### `careerops_integrations.hh`

Минимальная граница вокруг vendored `hh-applicant-tool`

`hh-applicant-tool/**` рассматривается как внешняя закреплённая зависимость и не меняется кодом CareerOPS

### `careerops_processing.contracts`

Единственный публичный набор versioned DTO Processing

### `careerops_processing.core`

Детерминированная бизнес-логика без PostgreSQL, S3, HTTP, gRPC, HH adapter и service runtime

### `careerops_processing.infrastructure`

Внешние границы Processing:

- PostgreSQL durable queue
- PostgreSQL semantic artifact registry
- PostgreSQL fenced current result publication
- S3 input/artifact stores
- HTTP client reranker
- `TargetPolicy` repository

### `careerops_reranker`

Отдельный GPU/model boundary. Не владеет queue, match decision или application actions

## PostgreSQL v2

Каноническая schema:

```text
careerops_v2
```

Миграции выполняются только через Alembic v2 lineage

```bash
alembic upgrade head
```

`processing_semantic_artifacts` является индексом переиспользования P2-04 semantic artifacts, а не заменой S3

`match_results` хранит только current pair projection P2-07, а immutable decision trace остаётся в S3

`application_candidates` является handoff projection и не означает, что отклик уже отправлен

## Артефакты Processing

До P2-07 включительно публикуются:

```text
INPUT_MANIFEST
FILTER_TRACE
REQUIREMENT_SET
RESUME_EVIDENCE_SET
P2_04_RESULT
EVIDENCE_CANDIDATE_SET
P2_05_RESULT
REQUIREMENT_QUALIFICATION_SET
P2_06_RESULT
MATCH_DECISION
P2_07_RESULT
```

Все артефакты неизменяемы и адресуются по SHA-256. Mutable `latest` pointers не используются

## Проверки

CI для pull request в `ar/processing-v2` включает:

- Ruff
- mypy
- dependency consistency
- fast pytest на Python 3.12
- fast pytest на Python 3.13
- PostgreSQL integration tests
- C++ matching-core build
- Python → C++ gRPC contract tests

PostgreSQL integration tests требуют отдельный disposable local/CI database target и не используют runtime DSN как fallback

## Документация

Актуальные документы Processing:

- `docs/processing/P2-03.md`
- `docs/processing/P2-03-evaluation.md`
- `docs/processing/P2-04.md`
- `docs/processing/P2-05.md`
- `docs/processing/P2-06.md`
- `docs/processing/P2-07.md`
- `infra/compose/reranker/README.md`

`docs/architecture-reset/progress/**` содержит только исторические снимки разработки и не является нормативным описанием текущей архитектуры
