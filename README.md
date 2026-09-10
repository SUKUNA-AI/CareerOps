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
                 EvidenceCandidateSet
                          │
                          ▼
                   будущий P2-06
                   qualification C++
                          │
                          ▼
                   будущий P2-07
                scoring / policy decision
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
- content-addressed Processing artifacts в S3
- production wiring `careerops-processing serve`
- C++20 `careerops-matching-core` с gRPC control plane и health/capability contract

Пока не реализованы как рабочие стадии Processing:

- P2-06 deterministic qualification
- P2-07 final scoring и policy decision
- Application Owner

`careerops-matching-core` пока не публикует фиктивный evaluator: capability API явно сообщает, что evaluate methods недоступны

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
          P205Executor
          │
          ├── P204Executor
          │   ├── S3ProcessingInputLoader
          │   ├── P2-03 filter
          │   ├── P204SemanticArtifactResolver
          │   │     └── separate PostgreSQL registry connection
          │   └── ProcessingArtifactPublisher
          │
          ├── ProcessingArtifactLoader
          ├── EvidenceCandidateSelector
          └── HttpJinaRerankerClient
                    │
                    ▼
             careerops-reranker
                    │
                    ▼
                Jina GPU
```

Queue и semantic registry используют разные PostgreSQL connections, чтобы heartbeat lease продолжал работать во время extraction, S3 и model calls

Health endpoints:

```text
/healthz  liveness процесса
/readyz   готовность полностью собранного Processing worker
```

Processing readiness означает, что зависимости и worker собраны. Временный model outage обрабатывается на уровне job как `DEFERRED`

## Отдельный GPU runtime

`careerops-reranker` находится в `src/careerops_reranker/`

PyTorch/Transformers не входят в обязательные зависимости основного Processing процесса. Для GPU runtime используется optional extra `reranker-runtime` и отдельный container из `infra/compose/reranker/`

Reranker endpoints:

```text
/healthz
/readyz
/v1/rerank
```

`/readyz` публикует фактическую runtime identity

## Processing runtime environment

До P2-05 включительно Processing использует:

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

P2-05 не требует matching-core target. Эта настройка должна появиться только вместе с P2-06

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

P2-05 не добавляет новую PostgreSQL domain table: его pair-specific результаты хранятся как immutable artifacts и durable job result URI

## Артефакты Processing

До P2-05 включительно публикуются:

```text
INPUT_MANIFEST
FILTER_TRACE
REQUIREMENT_SET
RESUME_EVIDENCE_SET
P2_04_RESULT
EVIDENCE_CANDIDATE_SET
P2_05_RESULT
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

`docs/architecture-reset/progress/**` содержит только исторические снимки разработки и не является нормативным описанием текущей архитектуры