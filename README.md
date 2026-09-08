# CareerOPS

CareerOPS — self-hosted платформа для сбора вакансий, хранения исходных данных, нормализации, фильтрации, сопоставления вакансий с резюме и последующей автоматизации откликов

Проект одновременно решает прикладную задачу поиска работы и используется как полноценный Data Engineering / Backend / ML Systems проект

Текущая ветка архитектуры построена вокруг явного разделения ответственности между источниками данных, хранилищем, нормализацией, Processing и вычислительным matching-core

## Текущий поток данных

```text
HH
│
│ read-only source adapter
▼
SeaweedFS S3
Immutable RAW
│
│ Spark normalization
│ планируемый production owner нормализации
▼
NormalizedVacancy / NormalizedResume
│
├──────────────► SeaweedFS S3
│                normalized bundles
│
└──────────────► PostgreSQL careerops_v2
                 current refs + domain/control state
                          │
                          ▼
                    Processing v2
                          │
                 deterministic filter
                          │
                 requirements / evidence
                          │
                        Jina
                          │
                          ▼
                careerops-matching-core
                     C++20 + gRPC
                          │
                          ▼
                   decision artifact
                          │
                          ▼
                 application candidate
                          │
                          ▼
                 Application Owner
                    будущий этап
```

На текущем этапе Spark normalization, полный semantic matching и Application Owner ещё не подключены в один production pipeline

При этом контракты Processing, durable queue, read-only HH ingestion и deterministic high-recall filter уже существуют как отдельные рабочие части архитектуры

## Главные архитектурные правила

### RAW неизменяем

Ответ источника сначала сохраняется в SeaweedFS S3 без смыслового преобразования

Каждое наблюдение имеет собственный immutable object key и SHA-256

Повторная публикация того же observation id допустима только при совпадении содержимого

```text
source response
↓
immutable RAW object
↓
normalization
```

PostgreSQL не является заменой RAW-слою

### PostgreSQL хранит состояние, а не исходную историю

Схема `careerops_v2` хранит:

- registry источников, аккаунтов и профилей
- текущие вакансии и резюме
- resume bindings
- source watermarks
- persistent source tasks
- Processing jobs
- текущее управляющее состояние
- ссылки на S3 artifacts и normalized bundles

Источником полной воспроизводимой истории остаётся S3

### Processing не читает произвольный текущий мир

Один Processing job получает точный `ProcessingInputManifest`

Manifest фиксирует:

- `NormalizedRef` вакансии
- `NormalizedRef` резюме
- `BindingSnapshot`
- `TargetPolicy`
- версии pipeline
- `as_of`

Из manifest вычисляется детерминированный `input_fingerprint`

Это позволяет повторно проверить решение на тех же входах, даже если текущая вакансия, резюме или policy уже изменились

### Один semantic decision unit — vacancy × binding

CareerOPS не выбирает одно «лучшее резюме» глобальным argmax

Одна вакансия может быть независимо обработана для нескольких bindings

```text
vacancy A × resume 1
vacancy A × resume 2
vacancy A × resume 3
```

Каждая пара имеет собственный Processing job и собственный результат

### Jina не принимает финальное решение

Jina используется как semantic relevance layer для выбора и ранжирования evidence

Низкий relevance score сам по себе не означает несовместимость

Отсутствие evidence не равно contradiction

Финальная квалификация должна выполняться детерминированной policy-логикой Processing и matching-core

## Структура Python-кода

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
│   ├── queue.py
│   ├── reconciliation.py
│   └── worker.py
└── careerops_storage/
    ├── s3.py
    └── v2/
```

### `careerops_adapter.hh`

Владеет HH source ingestion

Здесь находятся:

- persistent source tasks
- source worker
- watermarks
- публикация RAW
- HH transport boundary
- account-scoped worker composition
- seeding observation generations

Этот слой не фильтрует вакансии, не считает match и не отправляет отклики

### `careerops_integrations.hh`

Минимальная граница вокруг pinned `hh-applicant-tool`

Содержит только:

- TOML contracts source topology / discovery
- read-only CLI driver

`HHApplicantToolCLI` конструктивно поддерживает только GET-вызовы

Внутри CareerOPS нет POST-path через этот wrapper

### `careerops_processing.contracts`

Единый набор immutable DTO и versioned contracts Processing

Основные типы:

- `NormalizedVacancy`
- `NormalizedResume`
- `NormalizedRef`
- `RawObservationRef`
- `TargetPolicy`
- `BindingSnapshot`
- `ProcessingInputManifest`
- `FilterDecision`
- `ProcessingArtifactRef`

Missing и unknown значения не сворачиваются в `False`, пустую строку или пустой список

Для source-backed значений используется явное состояние:

```text
KNOWN
NOT_PROVIDED
EXPLICIT_NONE
UNKNOWN_PARSE
```

### `careerops_processing.core`

Чистая deterministic business logic

Core не должен зависеть от:

- PostgreSQL
- S3
- HTTP
- gRPC transport
- HH adapter
- service runtime

Сейчас здесь реализован P2-03 deterministic high-recall filter

### `careerops_processing.infrastructure`

Инфраструктурные реализации интерфейсов Processing

Сейчас здесь находятся:

- PostgreSQL durable queue store
- content-addressed artifact store
- artifact publisher
- `TargetPolicy` repository

### `careerops_processing.service`

Composition/runtime boundary отдельного `careerops-processing`

Команды:

```bash
python -m careerops_processing check-config
python -m careerops_processing serve
```

`serve` пока предоставляет постоянный service shell и health endpoint

`/readyz` намеренно остаётся `503`, пока реальный Processing executor не подключён к service runtime

Это лучше, чем сообщать ложную readiness до появления полного worker wiring

## HH source ingestion

HH adapter работает только на чтение

Фактический transport делегируется vendored `hh-applicant-tool`

```text
careerops_adapter.hh
↓
careerops_integrations.hh.driver.HHApplicantToolCLI
↓
hh-applicant-tool
↓
HH
```

### Конфигурация аккаунтов

Пример:

```text
config/hh_accounts.example.toml
```

Account config хранит только несекретную топологию:

- account key
- profile
- enabled state
- resume bindings
- source resume id
- target key
- binding version
- query sets
- policy-флаг автоматической отправки для будущего Application Owner

Секреты HH не должны попадать в CareerOPS TOML

### Discovery catalog

```text
config/hh_discovery.toml
```

Каталог задаёт поисковые запросы и только source request defaults:

- `area`
- `period`
- `per_page`
- `order_by`

Run-level budgets, искусственные лимиты количества вакансий и старые delay knobs в config отсутствуют

Throughput и повторная обработка управляются persistent source tasks и worker policy

### Seeding source generation

```bash
python -m careerops_adapter.hh seed \
  --account-key junior \
  --generation-id <UUID> \
  --kind all
```

Generation id задаётся orchestration layer снаружи

Повтор с тем же UUID должен переиспользовать уже созданный root task set, а не пересчитывать границы сканирования

### Выполнение source tasks

```bash
python -m careerops_adapter.hh work \
  --account-key junior \
  --max-tasks 25
```

`max-tasks` ограничивает один запуск worker, но не удаляет и не теряет persistent work

## Source task model

Исполняемые kinds:

```text
search_page
vacancy_fetch
resume_sync
resume_fetch
```

Состояния очереди:

```text
pending
claimed
running
deferred
retryable_failure
succeeded
terminal_failure
cancelled
```

Claim выполняется через PostgreSQL `FOR UPDATE SKIP LOCKED`

Lease защищён `lease_token`

Stale worker не должен иметь возможность подтвердить работу после потери lease

Search pagination строится из persistent child tasks

Watermark двигается только после успешной записи RAW и durable child work

## NormalizedRef и граница Spark → Processing

`vacancies` и `resumes` в PostgreSQL имеют projection, достаточную для восстановления точного `NormalizedRef`

В current projection входят:

- `raw_uri`
- `raw_sha256`
- `observed_at`
- `normalized_uri`
- `normalized_sha256`
- `semantic_content_hash`
- `normalized_schema_version`
- `normalization_version`
- `dictionary_version`
- `materialization_key`
- `dq_status`
- `processing_ready`

Для current normalized entity эти поля должны быть согласованы как единый контракт

`processing_ready=true` запрещён при `dq_status=blocked`

## Processing durable queue

Processing jobs живут в PostgreSQL и имеют точную identity:

```text
vacancy_id
binding_id
binding_version
input_fingerprint
input_manifest_uri
pipeline_version
policy_version
```

Для одной пары `vacancy_id × binding_id` может существовать только одна активная работа

Активные состояния:

```text
pending
claimed
running
deferred
retryable_failure
```

Reconciliation выполняется в порядке:

```text
pair advisory lock
↓
cancel competing active work
↓
insert / reuse / reactivate exact desired work
↓
commit
```

Ручная отмена и reconciliation-owned cancellation разделены

Работа, отменённая reconciliation как superseded/withdrawn, может быть реактивирована, если тот же exact input снова становится desired

Ручная отмена автоматически не реактивируется

Успешный Processing job обязан ссылаться на S3 result artifact

## P2-03 deterministic high-recall filter

Фильтр имеет только два semantic outcome:

```text
KEEP
EXCLUDE_PROVEN
```

`KEEP` не означает match

Он означает только одно: безопасное исключение не доказано

Фильтр работает fail-open для:

- отсутствующих данных
- неоднозначных title
- смешанных ролей
- неизвестного work format
- неполных location facts
- неразобранного experience

Hard exclusion разрешён только при явном доказательстве конфликта с pinned `TargetPolicy`

Каждый `EXCLUDE_PROVEN` содержит:

- `rule_id`
- `reason_code`
- `policy_version`
- source-backed evidence

Подробности:

```text
docs/processing/P2-03.md
docs/processing/P2-03-evaluation.md
```

## careerops-matching-core

```text
services/careerops-matching-core/
```

Отдельный C++20 service для deterministic compute

Production transport boundary:

```text
gRPC + Protobuf
```

Control protocol:

```text
proto/careerops/matching_core/v1/control.proto
```

Сейчас matching-core реализует только постоянный control plane:

- `Health`
- `GetCapabilities`

Полный evaluation RPC будет добавлен только после фиксации Requirement / ResumeEvidence / qualification contracts

Matching-core остаётся stateless и не владеет:

- PostgreSQL
- S3
- Jina
- job leases
- retries
- artifact publication
- HH side effects

Python Processing владеет orchestration

C++ service получает typed batch и возвращает deterministic compute result

## TargetPolicy

Версионированные policy-файлы находятся в:

```text
config/processing/target_policies/
```

`index.json` фиксирует current version каждого target

Policy content canonicalized и хешируется SHA-256

Processing job pinning использует конкретную `policy_version`, поэтому изменение current policy не переписывает смысл уже созданной работы

## S3 buckets

Архитектура различает несколько типов данных:

```text
careerops-raw
    immutable source observations

careerops-lake
    normalized data / будущие Spark outputs

careerops-artifacts
    immutable Processing artifacts
```

Конкретные bucket names задаются env и могут отличаться между окружениями

## PostgreSQL v2

Новая схема:

```text
careerops_v2
```

Она создаётся отдельной Alembic history и не является inplace-upgrade старой базы

Текущие migrations:

```text
20260906_v2_0001_foundation
20260906_v2_0002_source_watermarks
20260907_v2_0003_processing_active_pair
20260908_v2_0004_architecture_sanitization
```

`v2_0004` выполняет fail-safe preflight перед удалением устаревших структурных полей

Если в базе обнаружены неожиданные старые данные, migration должна остановиться, а не удалить их молча

## Environment

Пример переменных:

```text
.env.example
```

Основные группы:

```text
CAREEROPS_V2_POSTGRES_*
CAREEROPS_S3_*
CAREEROPS_HH_*
CAREEROPS_PROCESSING_*
```

Секреты нельзя коммитить в repository

## Локальная проверка

Установка:

```bash
python -m pip install -e '.[dev]'
```

Статические проверки:

```bash
python -m ruff check src tests
python -m mypy src
```

Fast tests:

```bash
python -m pytest -q -m "not integration_postgres"
```

PostgreSQL integration tests используют только явно заданный disposable target через:

```text
CAREEROPS_TEST_POSTGRES_DSN
```

Production DSN не должен использоваться как fallback для integration tests

## Docker Compose

Текущие compose units:

```text
infra/compose/seaweedfs/
infra/compose/processing/
infra/compose/matching-core/
```

Они разделены по ownership и не образуют старый единый application stack

## Что намеренно отсутствует в текущем runtime

В активной архитектуре нет отдельных Python-пакетов для старого orchestration, ETL и submission runtime

Нет старого `scripts/` execution layer

Нет старого HH worker deployment stack

Нет совместимого write-path поверх read-only HH driver

Новые capability добавляются только в тот service boundary, который ими владеет

## Историческая документация

`docs/architecture-reset/progress/` содержит только исторические снимки этапов перестройки

Они нужны для понимания принятых решений, но не являются нормативным описанием текущей системы

Актуальное состояние определяется:

1. исполняемым кодом
2. Alembic metadata текущего HEAD
3. этим README
4. актуальными документами `docs/processing/`

Если исторический progress-файл противоречит текущему коду или этому README, приоритет имеет текущая архитектура

## Vendored hh-applicant-tool

```text
hh-applicant-tool/
```

Это pinned внешний transport dependency

CareerOPS использует его для взаимодействия с HH и не дублирует внутри проекта его authentication/protocol implementation

Этот каталог рассматривается как vendored dependency и не изменяется в рамках обычного CareerOPS refactoring

## Ближайший Processing roadmap

```text
P2-03
Deterministic high-recall filter
        ↓
P2-04
Requirement / RequirementGroup / ResumeEvidence
        ↓
P2-05
Jina evidence ranking
        ↓
P2-06
MATCHED / NOT_EVIDENCED / UNKNOWN / CONTRADICTED
        ↓
P2-07
SKIP / REVIEW / APPLICATION_CANDIDATE
        ↓
P2-08
Application Owner
```

P2-07 должен заменить текущие промежуточные publication semantics `match_results` и `application_candidates` окончательным контрактом

До этого момента их schema не считается финальным semantic API
