# careerops-matching-core

`careerops-matching-core` — отдельный C++20 сервис детерминированных вычислений для Processing v2

Он разворачивается на `core` рядом с `careerops-processing`, а не на `edge`

Сервис намеренно stateless и не владеет:

- PostgreSQL
- S3
- Spark
- Jina
- job leases
- retries
- публикацией артефактов
- HH или application side effects

Python Processing владеет orchestration и после semantic reranking передаёт в matching-core типизированные batch requests

## Transport

Production boundary сервиса — gRPC + Protobuf

Постоянный control protocol находится в:

```text
proto/careerops/matching_core/v1/control.proto
```

На текущем этапе реализованы только:

- `Health`
- `GetCapabilities`

Полный evaluation protocol будет добавлен только после фиксации контрактов `Requirement`, `ResumeEvidence` и post-Jina qualification inputs

Это не позволяет закрепить временный wire contract, который пришлось бы сразу ломать на следующем этапе Processing

Будущий compute ownership включает:

- deterministic обработку role/technology features
- совместимость experience intervals
- evaluation requirement groups
- aggregation evidence и обработку конфликтов
- support bounds и mandatory coverage
- deterministic scoring и policy fan-out
- batched vacancy × resume evaluation

Инвариант сервиса:

```text
typed request
↓
deterministic compute
↓
typed response
```

## Network boundary

Адрес по умолчанию:

```text
127.0.0.1:50051
```

`careerops-processing` и matching-core используют host networking на `core`, поэтому Python service обращается к gRPC через host loopback

Не нужно открывать matching-core в LAN до отдельного проектирования transport authentication / TLS

При необходимости адрес можно переопределить:

```bash
CAREEROPS_MATCHING_CORE_LISTEN_ADDR=127.0.0.1:50051
```

## Build

```bash
docker compose -f infra/compose/matching-core/compose.yml build
```

Docker build использует Debian 13 build stage и переносит в итоговый Debian 13 image только binary и необходимые runtime shared libraries
