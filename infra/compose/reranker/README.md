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

## Развёртывание

```bash
sudo mkdir -p /etc/careerops/reranker
sudo mkdir -p /var/lib/careerops/reranker-cache
sudo chown 10001:10001 /var/lib/careerops/reranker-cache
sudo cp infra/compose/reranker/env.example /etc/careerops/reranker/env

docker compose -f infra/compose/reranker/compose.yml build
docker compose -f infra/compose/reranker/compose.yml up -d
```

Контейнер требует NVIDIA Container Toolkit и доступ к GPU через Compose `gpus: all`.

Model/cache хранится вне read-only container filesystem в `/var/lib/careerops/reranker-cache`.

## Проверка

```bash
curl http://127.0.0.1:18082/healthz
curl http://127.0.0.1:18082/readyz
```

`/readyz` становится доступен только после загрузки model runtime. В ответе присутствуют model revisions, backend, dtype, `torch_version` и `transformers_version`.

Processing должен использовать адрес этого сервиса через:

```text
CAREEROPS_PROCESSING_RERANKER_URL=http://<gpu-node>:18082
```

Для текущей двухузловой схемы пример на core указывает на edge по локальной сети.