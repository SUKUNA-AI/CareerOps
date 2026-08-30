# CareerOPS

CareerOPS — self-hosted платформа для автоматизации поиска работы и аналитики карьерного воронки.

Текущий MVP сфокусирован на HeadHunter:

- авторизация соискателя;
- поиск вакансий;
- получение полного JSON вакансии;
- нормализация вакансий в собственные контракты CareerOPS;
- отправка реальных откликов;
- защита от повторных откликов через состояние HH;
- дальнейшая запись RAW и событий откликов в S3-compatible storage.

## Текущий статус

Рабочий локальный контур:

```text
HH.ru
  ↓
hh-applicant-tool
  ↓
CareerOPS HH adapter
  ↓
RawVacancyRef / CanonicalVacancy
  ↓
application
```

Проверено на реальном аккаунте HH:

- OAuth авторизация;
- `whoami`;
- чтение резюме;
- поиск вакансий;
- получение полного объекта вакансии;
- реальные `POST /negotiations`;
- подтверждение отправки через `relations=["got_response"]`.

Следующий шаг — запуск CareerOPS на сервере и запись vacancy/application audit trail в SeaweedFS S3.

## Структура

```text
CareerOps/
├── hh-applicant-tool/          # vendored upstream HH integration
├── infra/
│   └── compose/
│       └── seaweedfs/
├── src/
│   ├── careerops_contracts/
│   └── careerops_integrations/
│       └── hh/
├── tests/
├── .gitignore
├── LICENSE
└── pyproject.toml
```

## Локальная установка

Требуется Python 3.12–3.13.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -U pip
python -m pip install -e .
python -m pip install -e .\hh-applicant-tool
python -m pip install playwright pillow
python -m playwright install chromium
```

Проверка:

```powershell
pytest -q
python -m hh_applicant_tool --help
```

## Профиль HH

Runtime state HH хранится в:

```text
hh-applicant-tool/config/<profile-id>/
```

Там находятся токены, cookies и SQLite-состояние. Эти файлы не должны попадать в Git.

Пример запуска:

```powershell
python -m hh_applicant_tool `
  --config-dir .\hh-applicant-tool\config `
  --profile careerops-ml `
  whoami
```

## HH adapter

CareerOPS не переimplementирует протокол HeadHunter.

`hh-applicant-tool` отвечает за:

- OAuth;
- токены и cookies;
- HH API;
- special web flows;
- отправку откликов.

CareerOPS отвечает за:

- собственные контракты;
- RAW ingestion;
- нормализацию;
- application audit trail;
- S3/PostgreSQL/ClickHouse;
- будущий reranking и LLM automation.

Интеграция построена поверх пользовательского CLI upstream и его SQLite discovery state, чтобы минимально зависеть от внутренних классов проекта.

## Безопасность

Никогда не коммитятся:

- HH access/refresh tokens;
- cookies;
- SQLite runtime state;
- S3 access/secret keys;
- `.env`;
- локальный `.careerops/`.

## Upstream

Для взаимодействия с HeadHunter используется проект:

**s3rgeym/hh-applicant-tool**  
https://github.com/s3rgeym/hh-applicant-tool

Автору и участникам upstream принадлежит реализация HH-specific логики. CareerOPS использует vendored snapshot проекта как внешний HH driver и добавляет собственный integration/data layer поверх него.

Зафиксированный на момент интеграции upstream revision:

```text
63210bcce74eb3e5cf6f2e994448675b38d2e8f9
```

При обновлении vendored копии revision следует обновлять в этом README.

## Инфраструктура

SeaweedFS используется как S3-compatible object storage.

Buckets:

```text
careerops-raw
careerops-lake
careerops-artifacts
```

На этапе разработки записи выполняются в `_lab/`, а production pipeline использует канонические prefixes.

## Roadmap ближайшего MVP

1. Развернуть CareerOPS на сервере.
2. Подключить реальный SeaweedFS S3.
3. Записывать vacancy/application RAW.
4. Автоматизировать поиск и отправку откликов с простыми hard-фильтрами.
5. Добавить PostgreSQL application state.
6. Добавить ClickHouse аналитику.
7. Позже — reranker и генерацию сопроводительных через LLM.
