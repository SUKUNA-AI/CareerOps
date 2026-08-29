# SeaweedFS

S3-compatible object storage для CareerOPS.

## Размещение

SeaweedFS работает на узле `edge`.

S3 endpoint:

- `http://127.0.0.1:8333` — локальный доступ на edge
- `http://10.42.0.1:8333` — внутренняя сеть CareerOPS

S3 публично не публикуется.

## Компоненты

- Master — topology и управление volumes
- Volume Server — физическое хранение данных
- Filer — namespace и metadata
- S3 Gateway — S3 API для приложений

Используется образ `chrislusf/seaweedfs:4.41`.

## Persistent data

Данные находятся вне lifecycle Docker-контейнеров:

- `/srv/careerops/seaweedfs/master`
- `/srv/careerops/seaweedfs/filer`
- `/srv/careerops/seaweedfs/volume`

Удаление и повторное создание контейнеров не удаляет данные.

## Buckets

- `careerops-raw` — неизменяемые исходные payload источников
- `careerops-lake` — будущие Silver/Gold Parquet и lakehouse данные
- `careerops-artifacts` — резюме, отчёты и другие артефакты

## Credentials

Реальный S3 IAM config находится на edge:

`/etc/careerops/seaweedfs/s3.json`

Он не хранится в Git.

В репозитории находится только `s3.example.json`.

Collector использует отдельную identity `careerops-collector`, ограниченную bucket `careerops-raw`.

## Запуск

Из каталога `infra/compose/seaweedfs`:

    docker compose up -d

Проверка:

    docker compose ps

Все четыре сервиса должны быть healthy.

## RAW

Canonical RAW не изменяется после записи.

Эксперименты выполняются только внутри prefix `_lab/`.
