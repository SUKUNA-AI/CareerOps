# Контейнер careerops-processing

`careerops-processing` — отдельный CPU/control-plane runtime Processing v2 на `core`.

До P2-05 включительно сервис владеет pair-specific orchestration, P2-03/P2-04, вызовом отдельного reranker service и публикацией воспроизводимых Processing artifacts. Spark, HH transport, GPU model runtime и `careerops-matching-core` остаются отдельными границами.

## Текущая ответственность

Сервис:

- читает durable Processing queue из PostgreSQL
- загружает pinned `ProcessingInputManifest`
- загружает pinned `NormalizedVacancy` и `NormalizedResume` из S3
- выполняет P2-03 filter
- получает или строит `RequirementSet` и `ResumeEvidenceSet`
- переиспользует semantic artifacts через PostgreSQL registry
- для P2-05 вызывает отдельный `careerops-reranker` по HTTP
- проверяет runtime identity reranker против `JinaVersionBundle` из manifest
- публикует `EvidenceCandidateSet` и `P205ResultArtifact`
- публикует immutable content-addressed Processing artifacts в S3

Сервис не загружает PyTorch/Transformers и не владеет GPU model lifecycle.

До P2-06/P2-07 сервис также не:

- вызывает `careerops-matching-core` для qualification
- публикует финальные match decisions
- создаёт application candidates
- выполняет Application Owner actions

## Сеть

Контейнер использует host networking, чтобы обращаться к PostgreSQL, SeaweedFS и reranker service по текущей LAN-топологии.

Health endpoint по умолчанию слушает только loopback:

```text
127.0.0.1:18081
```

Endpoints:

```text
/healthz  liveness процесса
/readyz   готовность полностью собранного ProcessingWorker
```

Docker healthcheck использует `/readyz`. Контейнер считается готовым только после открытия PostgreSQL/S3 dependencies, создания HTTP client reranker и сборки рабочего `ProcessingWorker`.

## Конфигурация

Runtime env хранится на узле в:

```text
/etc/careerops/processing/env
```

Шаблон находится в `infra/compose/processing/env.example`.

Для P2-05 обязательны:

```text
CAREEROPS_PROCESSING_RERANKER_URL
CAREEROPS_PROCESSING_RERANKER_TIMEOUT_SECONDS
CAREEROPS_PROCESSING_RERANKER_UNAVAILABLE_DELAY_SECONDS
```

`CAREEROPS_PROCESSING_RERANKER_URL` указывает на отдельный `careerops-reranker`, описанный в `infra/compose/reranker/`.

Processing использует собственные переменные `CAREEROPS_PROCESSING_S3_*`; стандартные `AWS_ACCESS_KEY_ID` и `AWS_SECRET_ACCESS_KEY` этим runtime не читаются.

`careerops-matching-core` target до P2-06 не требуется.

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

Если reranker временно недоступен, Processing process остаётся живым, а P2-05 job переводится в `DEFERRED` с повторной попыткой. Недоступность reranker не интерпретируется как отсутствие evidence.