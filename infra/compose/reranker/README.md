# careerops-reranker

Отдельный GPU-сервис P2-05. Он владеет Jina reranker runtime и не содержит Processing queue, PostgreSQL state или matching semantics.

## Граница ответственности

Сервис принимает один requirement query и список rendered `ResumeEvidence`, выполняет listwise reranking и возвращает только:

- индексы документов
- relevance score
- фактическую runtime identity
- фактическое число токенов запроса

Сервис не возвращает `MATCHED`, `NOT_EVIDENCED`, `UNKNOWN`, `CONTRADICTED`, итоговый score или application decision.

## Runtime identity

Model, custom code и tokenizer pinятся commit revision через `/etc/careerops/reranker/env`.

Версии `torch` и `transformers` не принимаются из env. `careerops-reranker` читает версии реально установленных Python distributions и возвращает их в `/readyz` и `/v1/rerank`. Processing сравнивает эту identity с `JinaVersionBundle`, зафиксированным в `ProcessingInputManifest`.

Проверенный runtime profile:

- `jinaai/jina-reranker-v3.5`
- pinned revision `e8a93f33f0b22108f8c2364f8484ce3422552fbc`
- PyTorch `2.14.0`
- CUDA runtime `13.2`
- cuDNN `9`
- Transformers `4.57.3`
- dtype `float16`

Профиль проверен на EDGE с NVIDIA GeForce GTX 1650: модель загружается, `/readyz` отвечает, реальный `/v1/rerank` выполняется на GPU, а запросы с CORE проходят по локальной сети к `192.168.0.10:18082`.

## Подготовка GPU-узла

Нужны Docker Engine, Docker Compose и NVIDIA Container Toolkit. Перед развёртыванием GPU должен быть доступен контейнеру:

```bash
docker run --rm --gpus all \
  pytorch/pytorch:2.14.0-cuda13.2-cudnn9-runtime \
  nvidia-smi
```

## Развёртывание

Использовать отдельный checkout репозитория. Не обновлять checkout, из которого уже работают другие production compose projects.

```bash
sudo install -d -m 0755 /etc/careerops/reranker
sudo install -d -o 10001 -g 10001 -m 0755 /var/lib/careerops/reranker-cache
sudo install -d -o 10001 -g 10001 -m 0755 /var/lib/careerops/reranker-cache/triton
sudo install -m 0644 \
  infra/compose/reranker/env.example \
  /etc/careerops/reranker/env

docker compose -f infra/compose/reranker/compose.yml config
docker compose -f infra/compose/reranker/compose.yml build reranker
docker compose -f infra/compose/reranker/compose.yml up -d reranker
```

Compose фиксирует image name `careerops-reranker:2.14-cu132-fp16`, поэтому тот же image можно использовать для предварительной загрузки model snapshot и диагностических запусков.

Контейнер использует read-only root filesystem. `/tmp` остаётся `noexec`. Triton JIT cache вынесен в `/cache/triton`, смонтированный с обычной executable filesystem хоста; это обязательно для загрузки JIT `.so`.

Model/cache хранится вне container filesystem в `/var/lib/careerops/reranker-cache`.

## Предварительная загрузка модели

Первый download можно выполнить отдельно от runtime, чтобы startup не зависел от скачивания весов:

```bash
docker run --rm \
  --network host \
  -e HF_HOME=/cache/huggingface \
  -e HF_HUB_ETAG_TIMEOUT=60 \
  -e HF_HUB_DOWNLOAD_TIMEOUT=300 \
  -v /var/lib/careerops/reranker-cache:/cache \
  careerops-reranker:2.14-cu132-fp16 \
  hf download \
    jinaai/jina-reranker-v3.5 \
    --revision e8a93f33f0b22108f8c2364f8484ce3422552fbc
```

Полный `HF_HUB_OFFLINE=1` пока не включается в production compose: Transformers `4.57.3` при загрузке tokenizer может выполнить `model_info()` даже при наличии локального snapshot. Веса при этом берутся из persistent HF cache.

## Проверка

```bash
docker compose -f infra/compose/reranker/compose.yml ps
curl -fsS http://127.0.0.1:18082/healthz
curl -fsS http://127.0.0.1:18082/readyz
nvidia-smi
```

`/readyz` становится доступен только после загрузки model runtime. В ответе присутствуют model revisions, backend, dtype, `torch_version` и `transformers_version`.

Для текущей двухузловой LAN-схемы CORE проверяет EDGE напрямую:

```bash
curl -fsS http://192.168.0.10:18082/healthz
curl -fsS http://192.168.0.10:18082/readyz
```

Processing, когда он будет развёрнут, должен использовать внутренний адрес GPU-сервиса:

```text
CAREEROPS_PROCESSING_RERANKER_URL=http://192.168.0.10:18082
```

## Эксплуатация

```bash
docker compose -f infra/compose/reranker/compose.yml restart reranker
docker compose -f infra/compose/reranker/compose.yml logs -f reranker
docker inspect \
  --format='status={{.State.Status}} health={{.State.Health.Status}} restarts={{.RestartCount}}' \
  careerops-reranker
```

`restart: unless-stopped` обеспечивает автоматический запуск контейнера после перезапуска Docker/EDGE. PostgreSQL, SeaweedFS и другие сервисы находятся в отдельных compose projects и этим compose-файлом не управляются.
