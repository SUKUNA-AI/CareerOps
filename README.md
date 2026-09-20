# CareerOPS

CareerOPS — self-hosted платформа для сбора вакансий, хранения исходных данных, нормализации и воспроизводимого сопоставления вакансий с резюме.

Проект одновременно решает прикладную задачу поиска работы и используется как Data Engineering / Backend / ML Systems проект.

## Нормативная архитектура

```text
HH
│
│ read-only source adapter
▼
SeaweedFS S3
Immutable RAW
│
│ Spark / Scala normalization
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
                          ▼ gRPC
                careerops-matching-core
                          │
               P2-06 qualification
                          │
               P2-07 scoring / policy
                          │
                          ▼
             SKIP / REVIEW / APPLICATION_CANDIDATE
                          │
                          ▼
                 Application Owner
                          │
                 safety / idempotency
                          │
                          ▼
                HH applicant transport
```

Spark владеет преобразованием RAW → normalized data. Processing не выполняет нормализацию и не меняет Spark contracts.

P2-06 и P2-07 принадлежат C++20 сервису `careerops-matching-core`. Python Processing отвечает за orchestration, PostgreSQL/S3, P2-03/P2-04/P2-05, Jina и gRPC boundary, но не содержит второго Python scorer/qualifier.

Application Owner владеет только финальным application workflow. Вендорский applicant transport является отдельной implementation island; CareerOPS взаимодействует с ним через собственный facade.

## Текущее состояние

Реализованы:

- read-only HH ingestion;
- immutable RAW в SeaweedFS S3;
- PostgreSQL schema `careerops_v2`;
- durable source tasks и watermarks;
- normalized input contracts для Spark → Processing;
- durable Processing queue с lease fencing и heartbeat;
- P2-03 deterministic high-recall filter;
- P2-04 `RequirementSet` и `ResumeEvidenceSet`;
- PostgreSQL registry для переиспользуемых P2-04 semantic artifacts;
- P2-05 `EvidenceCandidateSet` и отдельный Jina reranker runtime;
- pinned runtime identity для reranker;
- C++ `careerops-matching-core` с gRPC control/decision API;
- native P2-06 deterministic qualification;
- native P2-07 bounded scoring и policy decision;
- Processing replay через тот же native decision core;
- fenced publication `match_results` / `application_candidates`;
- content-addressed Processing artifacts в S3;
- P2-08 Application Owner;
- permanent account×vacancy submission guard;
- pre-submit stale-result fencing и безопасный rebind на новый current Processing result;
- post-submit uncertain/reconciliation state machine без blind retry;
- Application Owner lease heartbeat и operation deadline;
- bounded reconciliation fairness;
- PostgreSQL concurrency integration tests;
- real SeaweedFS S3 integration gate для RAW, Processing artifacts и Application audit.

Production calibration P2-07 намеренно остаётся отдельным release gate. До валидированной calibration автоматический `APPLICATION_CANDIDATE` не должен включаться произвольными thresholds.

Текущая ветка разработки проходит pre-deploy hardening. Наличие реализованного runtime не означает, что deployment объявлен готовым до завершения полного audit/CI gate.

## Основные инварианты

### RAW неизменяем

Ответ источника сначала сохраняется в SeaweedFS S3 без смыслового преобразования.

Каждое наблюдение получает точный RAW URI, SHA-256 и `observed_at`. Повтор того же observation с тем же содержимым идемпотентен; попытка записать другое содержимое под тем же RAW identity считается collision.

PostgreSQL не заменяет RAW-слой.

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

Spark записывает immutable normalized bundles в S3 и текущую проекцию в PostgreSQL. Processing получает normalized contracts как внешний вход.

### Processing работает только с зафиксированными входами

Один Processing job получает точный `ProcessingInputManifest`, содержащий refs вакансии и резюме, binding snapshot, policy/version bundle и `as_of`.

Из manifest вычисляется детерминированный `input_fingerprint`. После начала job Processing не должен подменять pinned входы произвольным текущим состоянием.

### Единица решения — vacancy × binding

Одна вакансия независимо рассматривается для каждого активного resume binding.

Каждая пара имеет собственный durable Processing job и current result.

### P2-03 исключает только доказанную несовместимость

P2-03 возвращает только:

```text
KEEP
EXCLUDE_PROVEN
```

Неопределённость остаётся `KEEP`.

### P2-04 строит семантические факты, но не решает match

P2-04 строит `RequirementSet` и `ResumeEvidenceSet` и сохраняет provenance, polarity, actor/context, logical groups и thresholds.

Он не выбирает evidence для конкретного requirement и не принимает итоговое решение.

### P2-05 — selector, а не judge

Jina получает high-recall evidence pool и возвращает ordered candidates. Relevance score не является qualification threshold и сам по себе не означает contradiction.

P2-05 не определяет `MATCHED / NOT_EVIDENCED / UNKNOWN / CONTRADICTED` и не принимает `SKIP / REVIEW / APPLICATION_CANDIDATE`.

### P2-06 и P2-07 имеют одного владельца

`careerops-matching-core` является единственным production implementation для deterministic qualification и scoring/policy.

```text
RequirementSet
ResumeEvidenceSet
EvidenceCandidateSet
TargetPolicy
        │
        ▼
C++ P2-06 / P2-07
        │
        ├── RequirementQualificationSet
        └── MatchDecision
```

Python не содержит permanent dual scorer. Replay использует тот же native core и pinned immutable artifacts.

### Qualification сохраняет неопределённость

P2-06 использует состояния:

```text
MATCHED
NOT_EVIDENCED
UNKNOWN
CONTRADICTED
```

Низкий Jina score не равен contradiction. Отрицательный вывод из отсутствия evidence разрешён только когда selection действительно exhaustive. Actor scope, polarity, evidence strength и duration квалифицируются детерминированно.

`ALL` агрегируется через минимум child support, `ANY` через максимум. `NOT_REQUIRED` не должен искусственно удовлетворять группу.

### P2-07 принимает bounded policy decision

P2-07 использует lower/upper support bounds, structured compatibility, mandatory gate и отдельный critical prohibited gate.

Доказанное critical prohibited contradiction является hard `SKIP`. Неопределённость не превращается в ноль. При отсутствии calibrated policy автоматический candidate закрыт fail-safe поведением.

### Current publication защищена fencing

Processing сначала публикует immutable artifacts, затем обновляет PostgreSQL current projection только под точной живой lease текущего job.

Supersede/withdraw/reconciliation инвалидируют stale current outputs. Старый worker не может вернуть устаревший result после потери lease.

### Application Owner не делает blind retry после возможного POST

До submit transport failure может стать safe retry.

После начала submit неизвестный исход становится `uncertain` / `reconciliation_required`; новый submit по тому же account блокируется, пока исход не разрешён.

Application transitions требуют точный живой lease. Во время внешней операции lease продлевается heartbeat, а сама operation имеет deadline меньше lease duration.

### Permanent guard не должен превращаться в dead-end до submit

`account_id × source_vacancy_id` имеет постоянный idempotency guard.

Если candidate устарел до submit и новый Processing result снова становится eligible, существующая pre-submit application может безопасно rebind к текущему result, не создавая второй guard и не выполняя blind submit.

После фактического или потенциального submit такой автоматический rebind запрещён.

### S3 является частью проверяемого runtime contract

CI содержит отдельный real SeaweedFS S3 integration boundary. Он проверяет не только mocks, но реальные S3-compatible put/get/head/list semantics, RAW collision behavior, Processing content addressing и Application audit persistence.

## Runtime services

Основные сервисы:

```text
careerops source ingestion
careerops-processing
careerops-reranker
careerops-matching-core
careerops-application-owner
PostgreSQL
SeaweedFS S3
```

Конкретные deployment manifests и operational settings находятся в `infra/`. Исторические документы в `docs/architecture-reset/progress/` не являются инструкцией по запуску текущего HEAD.

## Testing gates

Основной CI проверяет:

- architecture contracts;
- Ruff + mypy;
- Python 3.12/3.13 fast tests;
- PostgreSQL 18 integration;
- Alembic ↔ SQLAlchemy metadata consistency;
- real PostgreSQL state/concurrency semantics;
- C++ matching-core build;
- native gRPC decision contract;
- calibration/release contracts.

Отдельный storage workflow поднимает SeaweedFS 4.41 и выполняет реальные S3 integration tests.

Тесты в vendored `hh-applicant-tool` не смешиваются с CareerOPS test suite. CareerOPS тестирует собственный facade/contract вокруг vendor boundary.

## Документация

Приоритет источников текущей архитектуры:

1. исполняемый код текущего HEAD;
2. SQLAlchemy metadata и Alembic head `careerops_v2`;
3. этот `README.md`;
4. актуальные документы в `docs/processing/` и `docs/testing/`.

`docs/architecture-reset/progress/` содержит исторические snapshots и не должен использоваться как источник текущих runtime knobs или ownership boundaries.
