# CAR-147 — PostgreSQL integration CI foundation

Статус: текущая operational policy для PostgreSQL integration tests.
База реализации: `main`.

## Цель

CAR-147 создаёт независимую от конкретного поколения CareerOPS schema платформу для настоящих PostgreSQL integration tests.

Она отвечает за:

- воспроизводимый PostgreSQL service в GitHub Actions;
- каноническую версию PostgreSQL 18.6;
- один fail-closed boundary для `CAREEROPS_TEST_POSTGRES_DSN`;
- отдельный pytest execution boundary `integration_postgres`;
- JUnit и failure diagnostics;
- возможность Architecture Reset постепенно добавлять новые реальные DB cases без переписывания CI runner.

CAR-147 не фиксирует финальную PostgreSQL schema Architecture Reset и не пытается сохранить legacy v1 integration scenarios.

## CI runtime

Канонический CI service:

```text
postgres:18.6
```

Dedicated test database:

```text
careerops_ci_test
```

CI использует только `CAREEROPS_TEST_POSTGRES_DSN`. Runtime/production DSN fallback запрещён.

PostgreSQL job запускает:

```text
python -m pytest tests -q -p no:cacheprovider -m "integration_postgres"
```

Workflow поддерживает pull requests в:

```text
main
architecture-reset
```

После того как изменения CAR-147 окажутся в конкретной integration branch, её PR-ы могут использовать тот же PostgreSQL job.

## Disposable target safety

Общий guard расположен в:

```text
tests/support/postgres.py
```

Он обязан fail closed до любого destructive/integration DB action.

Проверяются:

- только явный `CAREEROPS_TEST_POSTGRES_DSN`;
- `host` и `hostaddr`, если оба присутствуют;
- только `localhost`, `127.0.0.1` или `::1`;
- явно disposable database name с отдельным marker `test`, `testing`, `ci` или `disposable`;
- запрет production-like и системных database names;
- отсутствие fallback на `CAREEROPS_POSTGRES_DSN`.

Таким образом закрывается найденный в CAR-140 gap, где friendly `host=localhost` мог сосуществовать с remote `hostaddr`.

## Базовые integration cases

CAR-147 оставляет только schema-neutral runner smoke tests:

1. CI действительно подключён к PostgreSQL 18.6 и ожидаемой disposable database.
2. Реальная PostgreSQL transaction rollback semantics работает на поднятом service.

Эти tests подтверждают сам integration runner, а не business/schema semantics CareerOPS.

## Legacy v1 cases

Старые real-PostgreSQL modules были привязаны к v1 bootstrap/cutover architecture:

```text
tests/test_alembic_postgres_integration.py
tests/test_postgres_integration.py
```

Они удалены из CAR-147 вместо переноса старой architecture в новую CI foundation.

Их содержательные требования не считаются автоматически потерянными: Architecture Reset добавляет новые integration cases там, где соответствующий контракт снова становится живым и стабильным.

## Как Architecture Reset расширяет платформу

Новые большие ветки добавляют реальные DB tests в тот же marker по мере стабилизации своих контрактов.

Примеры:

```text
ar/postgres-v2-foundation
  -> Alembic v2 / live constraints

ar/orchestration-airflow
  -> source_tasks lease / retry / defer semantics

ar/spark-data-pipeline
  -> staging / MERGE / current-state materialization

ar/processing-v2
  -> processing jobs / fencing / concurrency

ar/application-owner
  -> application guards / uncertain / reconciliation semantics
```

Не добавлять временные integration tests только ради покрытия промежуточной architecture. Тест должен защищать живой контракт текущей ветки.

## Failure behavior

Mandatory PostgreSQL CI не использует automatic retries и не превращает реальный failure в green rerun.

При падении сохраняются:

- JUnit report;
- Python version;
- PostgreSQL server version;
- current database;
- current user;
- pytest traceback.

Отсутствующий или unsafe test target не должен приводить к destructive fallback на другую PostgreSQL instance.
