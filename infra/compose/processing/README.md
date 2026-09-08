# Контейнер careerops-processing

`careerops-processing` — отдельный runtime Processing v2 на `core`.

На стадии P2-04 сервис владеет оркестрацией pair-specific jobs и публикацией воспроизводимых артефактов P2-03/P2-04. Spark, HH transport, Jina и вычислительная логика `careerops-matching-core` остаются отдельными границами.

## Текущая ответственность

Сервис:

- читает durable Processing queue из PostgreSQL
- загружает pinned `ProcessingInputManifest`
- загружает pinned `NormalizedVacancy` и `NormalizedResume` из S3
- выполняет P2-03 filter
- получает или строит `RequirementSet` и `ResumeEvidenceSet`
- переиспользует semantic artifacts через PostgreSQL registry
- публикует immutable content-addressed Processing artifacts в S3
- публикует `P204ResultArtifact` для конкретного job

На стадии P2-04 сервис **не**:

- вызывает Jina reranker
- вызывает `careerops-matching-core`
- публикует финальные match decisions
- создаёт application candidates
- выполняет Application Owner actions

Эти зависимости должны добавляться вместе с P2-05/P2-06/P2-07, а не заранее.

## Сеть

Контейнер использует host networking, чтобы обращаться к PostgreSQL и SeaweedFS по текущей LAN-топологии.

Health endpoint по умолчанию слушает только loopback:

```text
127.0.0.1:18081
```

Endpoints:

```text
/healthz  liveness процесса
/readyz   готовность полностью собранного ProcessingWorker
```

Docker healthcheck использует `/readyz`. Контейнер считается готовым только после открытия PostgreSQL/S3 dependencies и создания рабочего `ProcessingWorker`.

## Конфигурация

Runtime env хранится на узле в:

```text
/etc/careerops/processing/env
```

Шаблон находится в `infra/compose/processing/env.example`.

P2-04 использует собственные переменные `CAREEROPS_PROCESSING_S3_*`; стандартные `AWS_ACCESS_KEY_ID` и `AWS_SECRET_ACCESS_KEY` этим runtime не читаются.

Jina URL и matching-core target до P2-05/P2-06 не требуются.

## Сборка и проверка

```bash
docker compose -f infra/compose/processing/compose.yml build
python -m careerops_processing check-config
docker compose -f infra/compose/processing/compose.yml up -d
```

После запуска:

```bash
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
```
