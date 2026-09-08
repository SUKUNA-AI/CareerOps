# CareerOPS

CareerOPS — self-hosted платформа для сбора вакансий, хранения исходных данных, нормализации и воспроизводимого сопоставления вакансий с резюме

Проект одновременно решает прикладную задачу поиска работы и используется как Data Engineering / Backend / ML Systems проект

## Нормативная архитектура

Текущая архитектура разделяет источник данных, RAW-хранилище, нормализацию, управляющее состояние и Processing

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
                immutable semantic artifacts
                          │
                          ▼
                  следующие стадии
                  P2-05 / P2-06 / P2-07
```

Spark является владельцем преобразования RAW → normalized data

Processing не выполняет нормализацию и не меняет Spark contracts

## Состояние реализации

На текущем этапе реализованы:

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
- content-addressed Processing artifacts в S3
- production wiring `careerops-processing serve`
- C++20 `careerops-matching-core` с gRPC control plane и health/capability contract

Пока не реализованы как рабочие стадии Processing:

- P2-05 Jina evidence selection
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

Spark отвечает за:

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

Processing не должен читать произвольное «текущее» состояние после начала job

### Единица решения — vacancy × binding

Одна вакансия независимо рассматривается для каждого подходящего binding

```text
vacancy A × resume 1
vacancy A × resume 2
vacancy A × resume 3
```

Каждая пара имеет собственный durable Processing job

### P2-03 исключает только доказанную несовместимость

P2-03 возвращает:

```text
KEEP
EXCLUDE_PROVEN
```

Неопределённость остаётся `KEEP`

Низкая уверенность, отсутствие текста или отсутствие evidence не являются доказанным исключением

### P2-04 не принимает match decision

P2-04 строит две семантические проекции:

```text
NormalizedVacancy → RequirementSet
NormalizedResume  → ResumeEvidenceSet
```

`RequirementSet` сохраняет логическую структуру требований, включая:

- `ALL`
- `ANY`
- `CONDITIONAL`
- importance
- modality
- polarity
- activity
- context
- threshold
- provenance

`ResumeEvidenceSet` сохраняет:

- subject
- activity
- actor scope
- context
- polarity
- strength
- time span
- provenance

P2-04 не выбирает evidence для конкретного requirement и не решает, подходит ли кандидат

### Один semantic artifact на одну semantic version

`RequirementSet` не пересчитывается для каждого resume

`ResumeEvidenceSet` не пересчитывается для каждой vacancy

PostgreSQL таблица:

```text
careerops_v2.processing_semantic_artifacts
```

хранит semantic identity → `ProcessingArtifactRef`

Перед первой сборкой используется PostgreSQL advisory lock. Параллельные workers для одной semantic identity не выполняют extraction одновременно

Сами semantic artifacts остаются immutable и content-addressed в S3

Если registry содержит ссылку, а S3 object потерян, P2-04 допускает только детерминированную пересборку с точным совпадением прежнего `ProcessingArtifactRef`

### Ошибки данных и ошибки инфраструктуры различаются

Детерминированное нарушение semantic contract или artifact integrity приводит к terminal failure job

Временная ошибка PostgreSQL/S3 не маскируется под semantic failure и обрабатывается worker как retryable failure

### Jina не будет судьёй соответствия

P2-05 должен использовать Jina только для выбора и ранжирования candidate evidence относительно requirements

Низкий reranker score сам по себе не будет означать несовместимость

Финальные состояния `MATCHED / NOT_EVIDENCED / UNKNOWN / CONTRADICTED` относятся к P2-06, а `SKIP / REVIEW / APPLICATION_CANDIDATE` — к P2-07

## Production runtime Processing

Команды:

```bash
python -m careerops_processing check-config
python -m careerops_processing serve
```

`serve` собирает рабочий runtime:

```text
PostgreSQL queue connection
        │
        └── PostgresProcessingJobStore
                │
                ▼
        ProcessingWorker
                │
                ▼
          P204Executor
          ├── S3ProcessingInputLoader
          ├── P2-03 filter
          ├── P204SemanticArtifactResolver
          │     └── separate PostgreSQL registry connection
          └── ProcessingArtifactPublisher
```

Queue и semantic registry используют разные PostgreSQL connections, потому что heartbeat lease должен продолжать работать во время semantic extraction и S3 операций

Health endpoints:

```text
/healthz  liveness процесса
/readyz   готовность полностью собранного Processing worker
```

`/readyz` становится `200` только после успешного открытия PostgreSQL/S3 dependencies и сборки worker. При ошибке startup wiring сервис не объявляет ложную readiness

## Processing runtime environment

До P2-04 включительно обязательны:

```text
CAREEROPS_PROCESSING_POSTGRES_DSN
CAREEROPS_PROCESSING_S3_ENDPOINT_URL
CAREEROPS_PROCESSING_S3_ACCESS_KEY
CAREEROPS_PROCESSING_S3_SECRET_KEY
CAREEROPS_PROCESSING_S3_REGION
CAREEROPS_PROCESSING_NORMALIZED_BUCKET
CAREEROPS_PROCESSING_ARTIFACTS_BUCKET
CAREEROPS_PROCESSING_ARTIFACTS_PREFIX
CAREEROPS_PROCESSING_WORKER_ID
CAREEROPS_PROCESSING_WORKER_LEASE_SECONDS
CAREEROPS_PROCESSING_WORKER_IDLE_SLEEP_SECONDS
CAREEROPS_PROCESSING_HEALTH_HOST
CAREEROPS_PROCESSING_HEALTH_PORT
```

P2-04 не требует Jina URL или matching-core target. Эти настройки должны появиться только вместе со стадиями, которые реально используют соответствующие сервисы

Пример значений находится в `.env.example`

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
│   ├── semantic_cache.py
│   └── worker.py
└── careerops_storage/
    ├── s3.py
    └── v2/
```

### `careerops_adapter.hh`

Владеет read-only HH ingestion:

- persistent source tasks
- watermarks
- source worker
- публикация RAW
- account-scoped source orchestration

Этот слой не выполняет matching и не отправляет отклики

### `careerops_integrations.hh`

Минимальная граница вокруг vendored `hh-applicant-tool`

`hh-applicant-tool/**` рассматривается как внешняя закреплённая зависимость и не меняется кодом CareerOPS

### `careerops_processing.contracts`

Содержит единственный публичный набор versioned DTO Processing

Missing и unknown значения не схлопываются в falsey значения. Для source-backed данных используется `SourceValue`:

```text
KNOWN
NOT_PROVIDED
EXPLICIT_NONE
UNKNOWN_PARSE
```

### `careerops_processing.core`

Содержит детерминированную бизнес-логику

Core не зависит от:

- PostgreSQL
- S3
- HTTP
- gRPC transport
- HH adapter
- service runtime

### `careerops_processing.infrastructure`

Реализует внешние границы Processing:

- PostgreSQL durable queue
- PostgreSQL semantic artifact registry
- S3 normalized input loader
- content-addressed artifact store
- artifact publisher
- `TargetPolicy` repository

### `careerops_processing.service`

Собирает production dependencies и запускает `ProcessingWorker`

## PostgreSQL v2

Каноническая schema:

```text
careerops_v2
```

Миграции выполняются только через Alembic v2 lineage

```bash
alembic upgrade head
```

Текущий P2-04 добавляет таблицу:

```text
processing_semantic_artifacts
```

Она является индексом переиспользования, а не заменой S3

## Артефакты Processing

P2-03/P2-04 публикуют:

```text
INPUT_MANIFEST
FILTER_TRACE
REQUIREMENT_SET
RESUME_EVIDENCE_SET
P2_04_RESULT
```

Все артефакты неизменяемы и адресуются по SHA-256. Mutable `latest` pointers не используются

Текущие P2-04 schemas:

```text
careerops.processing.requirement-set.v2
careerops.processing.resume-evidence-set.v2
careerops.processing.p2-04-result.v2
```

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

`docs/architecture-reset/progress/**` содержит только исторические снимки разработки и не является нормативным описанием текущей архитектуры
