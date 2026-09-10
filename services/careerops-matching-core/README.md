# careerops-matching-core

`careerops-matching-core` — отдельный C++20 сервис для будущих оптимизированных детерминированных вычислений внутри Processing v2.

Он разворачивается на `core` рядом с `careerops-processing`, а не на `edge`.

Сервис намеренно stateless и не владеет:

- PostgreSQL;
- S3;
- Spark;
- Jina;
- job leases;
- retries;
- публикацией артефактов;
- semantic contracts P2-06/P2-07;
- policy/calibration ownership;
- HH или application side effects.

`careerops-processing` остаётся владельцем orchestration и канонической семантики P2-03–P2-07. Текущие P2-06 qualification и P2-07 scoring/policy decision реализованы в Processing и являются рабочим production contract.

## Transport

Production boundary сервиса — gRPC + Protobuf.

Постоянный control protocol находится в:

```text
proto/careerops/matching_core/v1/control.proto
```

На текущем этапе реализованы только:

- `Health`;
- `GetCapabilities`.

Evaluation capability намеренно отсутствует. `careerops-processing` не имеет runtime dependency на matching-core и не должен отправлять туда P2-06/P2-07 requests, пока не появится отдельный versioned compute protocol.

Если C++ backend будет подключён позже, он должен быть оптимизацией за уже зафиксированными Processing contracts, а не второй реализацией бизнес-семантики. Перенос вычислительно тяжёлых deterministic kernels не должен менять:

- `RequirementSet`, `ResumeEvidenceSet` и `EvidenceCandidateSet`;
- состояния `MATCHED / NOT_EVIDENCED / UNKNOWN / CONTRADICTED`;
- support-bound semantics;
- group semantics `ALL / ANY / CONDITIONAL`;
- hard-gate semantics;
- P2-07 score bounds и policy decision contract;
- artifact-first publication и replay identity.

Допустимый будущий ownership C++ ограничен чистыми детерминированными compute primitives, например batch aggregation или interval operations, для которых Python Processing остаётся владельцем входного/выходного контракта и проверяет capability/version identity.

Инвариант будущего compute path:

```text
versioned typed request
↓
deterministic compute primitive
↓
typed response
↓
validation by careerops-processing
```

До появления такого protocol matching-core остаётся control-plane skeleton и не участвует в принятии решений.

## Network boundary

Адрес по умолчанию:

```text
127.0.0.1:50051
```

Когда compute capability будет подключён, `careerops-processing` и matching-core должны использовать host networking на `core`, поэтому Python service будет обращаться к gRPC через host loopback.

Не нужно открывать matching-core в LAN до отдельного проектирования transport authentication / TLS.

При необходимости адрес можно переопределить:

```bash
CAREEROPS_MATCHING_CORE_LISTEN_ADDR=127.0.0.1:50051
```

## Build

```bash
docker compose -f infra/compose/matching-core/compose.yml build
```

Docker build использует Debian 13 build stage и переносит в итоговый Debian 13 image только binary и необходимые runtime shared libraries.
