# PostgreSQL v2 integration tests

Статус: актуальная test-platform для Architecture Reset / Processing v2.

## Назначение

`integration_postgres` проверяет только текущую PostgreSQL v2 архитектуру CareerOPS:

- pinned PostgreSQL 18.6 runner;
- чистый Alembic v2 lineage;
- schema `careerops_v2`;
- durable Processing jobs / leases / fencing;
- реальные PostgreSQL constraints и transaction semantics.

Legacy v1 cutover, `careerops` schema, старые OBSERVE/materializer/backfill contracts и
`careerops_storage.alembic_cutover` не являются частью этой платформы.

## Safety boundary

CI и локальные destructive integration tests получают только:

```text
CAREEROPS_TEST_POSTGRES_DSN
```

Test helper принимает target только если:

- host/hostaddr явно loopback;
- database name явно содержит `test`, `testing`, `ci` или `disposable`;
- database не выглядит production-like;
- libpq `service` indirection не используется.

Нет fallback на:

```text
CAREEROPS_V2_POSTGRES_DSN
CAREEROPS_POSTGRES_DSN
```

После validation fixture временно pin-ит уже проверенный DSN в
`CAREEROPS_V2_POSTGRES_DSN` только для Alembic v2.

## CI

GitHub Actions job `PostgreSQL integration` поднимает:

```text
postgres:18.6
database: careerops_v2_ci
```

и выполняет:

```bash
python -m pytest -q -p no:cacheprovider -m "integration_postgres"
```

Real-PostgreSQL boundary содержит три группы.

### Runner smoke

`tests/test_postgres_ci_integration.py`

Проверяет фактический PostgreSQL 18.6 и реальный rollback транзакции.

### Alembic v2

`tests/test_alembic_postgres_integration.py`

Проверяет fresh database -> current v2 head, current `careerops_v2` tables,
идемпотентный `upgrade head`, fail-closed отказ от legacy markers и round-trip
`head -> base -> head`.

FAST companion `tests/test_alembic_migrations.py` проверяет граф ревизий и offline SQL
без runtime DSN.

### Processing durable jobs

`tests/test_processing_postgres_integration.py`

Проверяет на настоящем PostgreSQL idempotent reconciliation, supersession,
reconciliation-owned reactivation, manual cancellation, withdrawal fencing,
lease reclaim, stale-worker fencing, deferred scheduling, artifact-first success,
single-active-job invariant и migration preflight.

## Локальный запуск

Поднять отдельный disposable PostgreSQL 18.6 и задать:

```powershell
$env:CAREEROPS_TEST_POSTGRES_DSN = "postgresql://...@127.0.0.1:5432/careerops_v2_test"
```

Затем:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m "integration_postgres"
```

FAST suite PostgreSQL не требует:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m "not integration_postgres"
```

## Правило Architecture Reset

Новые PostgreSQL integration cases добавляются рядом с owning subsystem.
Не возвращать тесты старой v1 архитектуры только ради historical coverage.
Если production implementation удалён как legacy и не имеет живых consumers, его тесты
также не являются частью текущего release gate.
