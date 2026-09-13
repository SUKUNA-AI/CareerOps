# P2-05 — Jina observability и audit

Этот документ дополняет `P2-05.md`. Он описывает эксплуатационную наблюдаемость reranker и не меняет семантику matching.

## Граница ответственности

`careerops-reranker` остаётся GPU inference runtime. Он получает query, rendered evidence documents, `top_n`, token budget и exact expected runtime identity. Он не знает vacancy/resume business semantics и не принимает решения `MATCHED`, `SKIP` или `APPLICATION_CANDIDATE`.

Processing владеет operational audit, потому что именно Processing знает `processing_job_id`, vacancy × binding и `requirement_id`.

## Correlation

Для каждого фактического listwise вызова Processing генерирует UUID `run_id`. Тот же UUID передаётся в HTTP request как `request_id` и попадает в structured log GPU runtime.

## S3 audit

Каждый вызов создаёт отдельный append-only bundle:

```text
careerops-artifacts/
  reranker/
    run_id=<uuid>/
      request.json
      response.json
      metrics.json
```

`request.json` содержит:

- `run_id`;
- `processing_job_id`;
- `vacancy_id`;
- `binding_id`;
- `requirement_id`;
- rendered requirement query;
- ordered evidence IDs;
- SHA-256 rendered pool;
- фактически запрошенный `top_k`;
- token budget;
- expected runtime identity.

Rendered evidence texts в audit bundle повторно не сохраняются. Они восстанавливаются из immutable Processing semantic artifacts.

`response.json` содержит actual runtime identity, selected evidence IDs/ranks/raw Jina relevance scores и exact `total_tokens`.

`metrics.json` содержит started/finished timestamps, latency, pool/selected sizes, status/error class, `token_budget_utilization` и `selection_ratio`.

## PostgreSQL

`careerops_v2.reranker_runs` — только searchable operational index. Он не дублирует полный S3 payload.

Основные поля:

```text
id = run_id
processing_job_id
vacancy_id
binding_id
requirement_id
status
pool_size
top_k
selected_size
total_tokens
latency_ms
runtime_fingerprint
artifact_uri
error_class
started_at
finished_at
created_at
```

Контекст job определяется по активной Processing job с exact `input_fingerprint`; неоднозначность является protocol failure, а не silent fallback.

## Structured logs

`careerops-reranker` пишет JSON lines. Для rerank request логируются только operational fields:

```json
{
  "event": "reranker_request",
  "request_id": "...",
  "documents": 18,
  "top_n": 5,
  "tokens": 842,
  "latency_ms": 123,
  "status": "success",
  "error_class": null
}
```

Query, vacancy text, resume text и rendered evidence documents в обычные application logs не пишутся.

## Prometheus

Endpoint:

```text
GET /metrics
```

Экспортируются:

```text
reranker_requests_total
reranker_errors_total{error_class=...}
reranker_request_duration_seconds
reranker_tokens_total
reranker_documents_per_request
reranker_selected_per_request
```

IDs вакансий, резюме, requirements и run IDs не используются как Prometheus labels, чтобы не создавать high-cardinality series.

## Offline quality metrics

Ranking quality нельзя корректно вычислять online без ground truth. После появления calibration labels evaluator использует immutable candidate sets и audit data.

Primary:

- `Recall@1`, `Recall@3`, `Recall@5` — доля ожидаемых evidence, найденных в top-k;
- `MRR` — насколько высоко находится первый релевантный evidence;
- `NDCG@k` — качество порядка при graded relevance labels.

Diagnostic:

- `Precision@k`;
- `AP@k`, а среднее по requirements — `MAP@k`;
- `Hit@k` для простого found/not-found контроля.

`src/careerops_processing/evaluation/reranker_metrics.py` содержит детерминированные функции этих метрик. Никакая из них не влияет на runtime decision P2-05/P2-06/P2-07.

## Failure semantics

Успешный inference без durable audit не считается полноценным P2-05 успехом. S3/PG audit infrastructure failure маппится в reranker unavailable/deferred path. Нарушение audit identity/invariants считается protocol error.

Это сохраняет основной инвариант: любой использованный Jina результат должен иметь воспроизводимый operational след.
