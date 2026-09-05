# CAR-140 — Audit and classify existing CareerOPS tests

Feature: **CAR-139 — Test suite architecture and cleanup**. Дата аудита: **2026-09-05**.

Аудируемый snapshot: branch `CAR-139-test-suite-architecture-cleanup`, HEAD `7459aa88c776db0b4410446fbcfaf53b9b0056aa`. На момент начала tracked working tree чист; единственный untracked файл — пустой целевой отчёт. Read-only проверка GitHub показала тот же commit на `main`; remote branch этой задачи не обнаружен.

**Статус документа:** аналитический результат, не выполненная реорганизация. В CAR-140 изменяется только этот файл. В рамках этого аудита production, tests, pytest configuration, CI, legacy SQL и Alembic revisions не изменялись. Новые tests, fixtures, markers, migrations и будущие V2-компоненты не создавались.

**Изменение workspace во время аудита:** временно наблюдалась ветка `CAR-47-lossless-hh-search-scheduler-v2` при том же HEAD; после сообщения пользователя о переключении очередная сверка показала `CAR-139-test-suite-architecture-cleanup`. Параллельные изменения `src/careerops_storage/schema.py` и новый revision `20260905_0048_car_48_add_search_query_states.py` сохранены без вмешательства. Анализ и 208 passed относятся к исходному чистому snapshot, а не к последующему незавершённому V2 working tree. Со стороны CAR-140 создан только отчёт; checkout/commit/push не выполнялись.

## 1. Executive summary

Suite уже достаточно быстрый: **219 collected, 208 fast, 11 PostgreSQL integration**; повторный локальный fast run дал **208 passed за 2.44 s** против переданного baseline около 2.35 s. Цель cleanup — качество доказательства production invariants, ясное владение ответственностями и безопасный переход к V2. Уменьшение test count или повышение coverage percentage не является целью.

Полностью прочитаны 24 test modules, `tests/conftest.py`, `pyproject.toml`, `.github/workflows/ci.yml`, проверяемые HH/ETL/storage/scheduler modules, Alembic configuration/baseline, все SQL migrations 0001–0005 и relevant history. **196 test functions / 219 collected cases** разобраны в **125 семантических группах** ниже. Названия вроде `integration`, `atomic`, `rollback` не принимались за доказательство соответствующего уровня.

Первичная классификация каждого collected case ровно одна:

- **141 KEEP**: действующие behavioral, regression и contract checks.
- **30 REWRITE**: полезный invariant с хрупким или недостаточным oracle.
- **5 MERGE + 1 DELETE_NOW**: только шесть кандидатов сокращения; у пяти MERGE уникальные assertions сначала должны перейти к владельцу scenario.
- **30 RETIRE_AFTER_V2**: 29 примеров временного precision title/prefilter gate и один compound OBSERVE technical-bounds scenario. До соответствующего cutover они продолжают выполняться.
- **5 SPLIT + 7 MOVE**: полезная защита с неудачной границей ответственности/расположением.

Отдельно выделены **30 MISSING CURRENT REGRESSIONS** — логические группы текущих недостающих/недостаточных доказательств, не обещание ровно 30 новых functions — и **12 FUTURE_CONTRACTS**. Последние активируются только с реализацией соответствующего Feature; это не failing/xfail tests для сегодняшнего production.

Ключевые выводы:

1. **Важнейшие слепые зоны находятся между компонентами.** Изолированные probes actual production paths подтвердили: uncertain APPLY outcome не читается ETL; failure сохранения claim после POST позволяет превысить allocation; CAPTCHA POST не передаётся в pause logic. Нормальные fake summaries скрывают эти дефекты.
2. **Большая часть SQL unit suite проверяет намерение, а не эффект.** ON CONFLICT strings, params positions и fake rows не доказывают idempotence, latest-wins, stale-owner protection или atomic reservation. Real PostgreSQL tests есть, но runtime setup использует legacy SQL, а ETL integration копирует loader в helper.
3. **Реальные safety layers нельзя сливать как дубли.** Service/driver/direct bridge guards, precheck/after-POST uncertainty, first/second resume, partial/full filtering boundaries и fake-vs-real-DB concurrency защищают разные пути регрессии.
4. **Есть текущий инфраструктурный риск integration tests.** Один fixture пропускает remote `hostaddr` при `host=localhost` к destructive DB setup; другой validator этот DSN отвергает. Унификация должна начинаться с safety policy, не с общего `conftest.py`.
5. **Шесть модулей больше 500 строк; reuse надо доказывать.** Есть 3 подтверждённые семейства повторяемой инфраструктуры (13 реализаций, 10 потенциально избыточных копий), но не все похожие fakes взаимозаменяемы. Большие versioned HH payloads и независимые schema expectations нельзя механически генерировать из production.
6. **Legacy и V2 надо разделить по смыслу.** Current ingestion и replay RAW-v2/v3 сохраняются. Head-wide запрет V2-таблиц и parser только старых SQL migrations следует переписать; не нужно превращать временные title decisions в постоянный контракт discovery.

## 2. Baseline и метод проверки

| Метрика | Переданный baseline | Проверка CAR-140 |
|---|---:|---:|
| Total collected | 219 | **219**, collection 0.66 s |
| Fast cases | 208 | **208 passed**, 11 deselected |
| PostgreSQL integration cases | 11 | **11 collected**, не запускались |
| Fast pytest duration | ~2.35 s | **2.44 s**, один локальный прогон |
| Wall time процесса fast run | Не задан | ~3.20 s; это другая метрика |
| Test modules | — | 24 |
| Test function definitions | — | 196; parametrization добавляет 23 collected cases |
| Python | — | Local `.venv`: **3.13.13**, Windows/PowerShell |
| Строки `tests/*.py` | — | 7,825 с 36 строками conftest; 7,789 в test modules |
| Oversized modules | — | **6** при диагностическом пороге >500 строк |
| Длинные test functions | — | 8 при диагностическом пороге >80 строк |
| Top-level infrastructure inventory | — | 41 class definitions, 59 non-test functions/fixtures; это **не** число duplicates |

Команды выполнены из корня репозитория, с отключённой записью bytecode и pytest cache:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
.\.venv\Scripts\python.exe -B -m pytest --collect-only -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider -m "not integration_postgres" --durations=10
```

Это проверка current snapshot, не CI benchmark и не statistically significant speed comparison. Два subprocess Alembic cases занимали примерно по 0.76 s; их реальная граница процесса оправдана, заменять её mock ради скорости не предлагается. PostgreSQL/SeaweedFS/HH не поднимались и внешние application requests не выполнялись; **11 integration tests нельзя считать passed по этому аудиту**.

Дополнительно выполнены ephemeral Python probes в памяти с existing test doubles, injected failures и production functions. Они не добавлены в suite, не создавали test files и не подключались к DB/HH/S3. Результаты и границы доказательства приведены в разделе 8. Точность подсчётов проверяется по collected node IDs, а не числу `def test_`.

## 3. Current test architecture

`pyproject.toml` ограничивает discovery основным `tests/`, добавляет `src` в pythonpath и определяет единственный собственный marker `integration_postgres`. Понятия unit/component/integration пока не отражены каталогами или устойчивой naming policy. 208 fast cases — смесь чистых функций, storage/CLI adapters, orchestration с fakes, локального filesystem и subprocess smoke, а не «208 unit tests».

| Текущая область | Фактическое доказательство | Чего из него не следует |
|---|---|---|
| Filtering, letters, reconciliation, planner | Domain decisions, pure transformations, selected config scenarios | Полнота future matching, DB persistence или реальный HH transport |
| Application audit, OBSERVE, account orchestration | Current workflow через fake external/storage ports | Atomicity реального PG, S3 immutability, правильность нижележащего fake algorithm |
| ETL/backfill/materializer | Wire validation, mapping и вызовы sink/transaction scopes | End-to-end DB rollback/idempotence actual loader |
| PostgreSQL storage fast tests | SQL string/params shape и response decoding | Реальное состояние rows/constraints/concurrent transactions |
| Schema metadata tests | Independent declarative types/keys/nullability/defaults/indexes | Успешное исполнение runtime SQL на текущем Alembic head |
| Alembic fast tests | Revision graph, guarded config, offline CLI, cutover orchestration | Populated DB migration/data preservation |
| Два `integration_postgres` модуля | Реальные SQL/DDL/constraints; partial safety integration | Сквозной actual RAW producer → ETL → canonical DB и весь recovery matrix |

Текущий production workflow надо читать в двух режимах:

- **OBSERVE:** authoritative resume reconciliation → query-window reservation → broad query/page union/dedup → RAW pages/search item/full vacancy и provenance/evaluation sidecars → отдельная S3→PG materialization. Legacy ML precision filter не применяется к OBSERVE discovery.
- **APPLY:** динамический account/resume selection и allocation → действующий legacy prefilter/full validation/cover letter → guarded audited service → canonical vacancy preparation → pair claim → exact evidence check → external submission/confirmation.
- **Storage:** immutable RAW является заявленным invariant; metadata/source purity реализованы и частично проверены, техническое запрещение повторного overwrite S3 key не доказано и отсутствует на writer boundary. PostgreSQL хранит OLTP/current state и claims; canonical schema задаётся SQLAlchemy Core/Alembic, runtime использует psycopg explicit SQL.
- **Scheduler:** сейчас authority для timetable/slot/day quota — JSON в `state_dir` и lock; S3 mirror best-effort. Это реально существующее исключение относительно будущего полного PostgreSQL control plane, его нельзя скрывать в классификации.

`.github/workflows/ci.yml`: PR в main; quality job на Python 3.12 (`ruff` для src/scripts/tests, `mypy src`, `pip check`), fast matrix Python 3.12/3.13 с исключением `integration_postgres`, JUnit/report artifacts, cache и cancellation. **PG integration gate сейчас отсутствует.** CAR-140 не меняет CI; дальнейшая интеграция DB checks зависит от безопасных fixtures и явного disposable infrastructure setup.

### tests/conftest.py

36 строк, два узких механизма: Windows SelectorEventLoop factory для psycopg/pytest-asyncio и `workspace_tmp_dir` с уникальным каталогом под `.careerops/pytest-temp` и cleanup. **KEEP текущую область ответственности.** Event-loop workaround не считать лишним platform detail. Локальные fixtures не следует собирать сюда только потому, что root conftest автоматически виден всем. Поведение cleanup при failure не является доказательством DB rollback.

### Vendored hh-applicant-tool

`hh-applicant-tool/tests/` — отдельные 8 upstream test modules (AI prompt, apply-limit shutdown, start, UI/API/presets/window/launcher, XSRF), вне основного `testpaths`. Их counts не включены в 219. Boundary проверяется нашими driver/test_bridge tests. `THIRD_PARTY.md` фиксирует upstream revision `63210bcce74eb3e5cf6f2e994448675b38d2e8f9`.

Не объединять suites, dependencies или coverage без отдельного решения о сопровождении vendor snapshot. Обновления upstream должны иметь собственную smoke/compatibility проверку и отдельный результат, без скрытого включения в fast CareerOPS gate.

## 4. Inventory по test modules

Ниже «PG» означает текущий marker, а не слово integration в имени. KEEP тоже может впоследствии перемещаться вместе с модулем: первичный verdict отражает главное необходимое действие над case.

| Module | Строк | Collected (fast / PG) | Первичные verdicts |
|---|---:|---:|---|
| [test_alembic_baseline.py](../../tests/test_alembic_baseline.py) | 262 | 6 (6 / 0) | KEEP 1; REWRITE 4; MERGE 1 |
| [test_alembic_cutover.py](../../tests/test_alembic_cutover.py) | 378 | 21 (21 / 0) | KEEP 21 |
| [test_alembic_migrations.py](../../tests/test_alembic_migrations.py) | 58 | 1 (1 / 0) | KEEP 1 |
| [test_alembic_postgres_integration.py](../../tests/test_alembic_postgres_integration.py) | 159 | 5 (0 / 5) | KEEP 4; DELETE_NOW 1 |
| [test_backfill_hh_postgres.py](../../tests/test_backfill_hh_postgres.py) | 163 | 5 (5 / 0) | KEEP 5 |
| [test_hh_account_orchestration.py](../../tests/test_hh_account_orchestration.py) | 218 | 2 (2 / 0) | MOVE 2 |
| [test_hh_application_audit.py](../../tests/test_hh_application_audit.py) | 585 | 16 (16 / 0) | KEEP 15; MERGE 1 |
| [test_hh_configuration.py](../../tests/test_hh_configuration.py) | 328 | 11 (11 / 0) | KEEP 7; REWRITE 3; SPLIT 1 |
| [test_hh_cover_letters.py](../../tests/test_hh_cover_letters.py) | 50 | 3 (3 / 0) | KEEP 3 |
| [test_hh_filtering.py](../../tests/test_hh_filtering.py) | 226 | 39 (39 / 0) | KEEP 10; RETIRE_AFTER_V2 29 |
| [test_hh_mapper.py](../../tests/test_hh_mapper.py) | 45 | 1 (1 / 0) | SPLIT 1 |
| [test_hh_observe.py](../../tests/test_hh_observe.py) | 708 | 7 (7 / 0) | KEEP 5; RETIRE_AFTER_V2 1; SPLIT 1 |
| [test_hh_raw.py](../../tests/test_hh_raw.py) | 72 | 2 (2 / 0) | REWRITE 2 |
| [test_hh_resume_sync.py](../../tests/test_hh_resume_sync.py) | 338 | 16 (16 / 0) | KEEP 11; MERGE 1; MOVE 4 |
| [test_hh_runtime.py](../../tests/test_hh_runtime.py) | 266 | 18 (18 / 0) | KEEP 16; REWRITE 1; SPLIT 1 |
| [test_hh_s3_to_postgres.py](../../tests/test_hh_s3_to_postgres.py) | 942 | 16 (16 / 0) | KEEP 13; REWRITE 2; SPLIT 1 |
| [test_materialize_hh_pending.py](../../tests/test_materialize_hh_pending.py) | 119 | 1 (1 / 0) | KEEP 1 |
| [test_postgres_integration.py](../../tests/test_postgres_integration.py) | 686 | 6 (0 / 6) | KEEP 4; REWRITE 1; MERGE 1 |
| [test_postgres_storage.py](../../tests/test_postgres_storage.py) | 802 | 16 (16 / 0) | KEEP 3; REWRITE 12; MERGE 1 |
| [test_project_dependencies.py](../../tests/test_project_dependencies.py) | 14 | 1 (1 / 0) | KEEP 1 |
| [test_s3_listing.py](../../tests/test_s3_listing.py) | 254 | 9 (9 / 0) | KEEP 9 |
| [test_scheduler_dispatcher.py](../../tests/test_scheduler_dispatcher.py) | 306 | 6 (6 / 0) | KEEP 4; REWRITE 1; MOVE 1 |
| [test_scheduler_planner.py](../../tests/test_scheduler_planner.py) | 96 | 4 (4 / 0) | KEEP 2; REWRITE 2 |
| [test_storage_schema.py](../../tests/test_storage_schema.py) | 714 | 7 (7 / 0) | KEEP 5; REWRITE 2 |

## 5. Семантический аудит каждого module

В таблицах перечислены все test functions; квадратные пометки означают число collected параметров. Несколько functions объединены только при одинаковом verdict и явно описанной общей ответственности. Имена приведены для трассировки, решение основано на прочитанном теле tests и production.

### 5.1. test_alembic_baseline.py

**Domain:** Migration baseline и запуск Alembic. **Production:** alembic.ini; alembic/env.py; alembic/versions/20260904_0005_current_schema_baseline.py; pyproject.toml.

**Фактический layer:** Смесь unit DDL recorder, config contract и двух subprocess component checks.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_alembic_dependency_and_safe_project_configuration` | 1 | REWRITE | Alembic доступен, конфигурация привязана к корню проекта и не содержит DSN/credentials. Проверка точной строки версии зависимости блокирует штатное обновление; оставить семантику зависимости и безопасной конфигурации. |
| `test_canonical_baseline_remains_a_graph_root` | 1 | MERGE | Baseline 20260904_0005 остаётся корнем без предшественника. Включить проверку baseline и, если требуется, branch_labels в единый graph test; сейчас graph проверяется повторно. |
| `test_baseline_upgrade_is_self_contained` | 1 | REWRITE | Upgrade записывает собственные DDL-операции в careerops. Recorder проверяет форму вызовов, но слабо доказывает независимость от изменяемого metadata; проверять frozen revision отдельно от текущего head. |
| `test_baseline_downgrade_removes_indexes_tables_and_schema` | 1 | REWRITE | Удаление объектов baseline. Требование сначала вызвать все drop_index, потом drop_table — деталь реализации; физический результат уже проверяет PostgreSQL round trip, самостоятельную историю baseline сохранить. |
| `test_offline_upgrade_works_outside_checkout_from_installed_package` | 1 | REWRITE | Реальный subprocess запускает offline Alembic вне checkout без PYTHONPATH. Сохранить этот packaging/config boundary; удалить запрет V2-таблиц из результата всего head, ограничив исторические утверждения baseline. |
| `test_missing_database_url_fails_without_attempting_a_connection` | 1 | KEEP | Без URL offline-команда завершается с явной ошибкой. Это useful fail-closed smoke, но --sql сам по себе не доказывает отсутствие подключения в online-ветке. |

**Duplicated infrastructure / payloads:** _config повторён в test_alembic_migrations; UpgradeRecorder/DowngradeRecorder имитируют Alembic operations. _normalized_sql повторён в schema tests, но extraction двух строк не самоцель.

**Missing current regressions:** R27–R28; online URL precedence/отсутствие production fallback входят в R05.

**Proposed action / проблемные проверки:** Разделить migration graph, frozen baseline contract и offline CLI. Сохранить запуск вне checkout; он проверен на текущей установленной venv, не доказывает отдельную wheel installation. Убрать head-wide V2 blacklist (строки 232–233).

### 5.2. test_alembic_cutover.py

**Domain:** Guarded cutover validation. **Production:** src/careerops_storage/alembic_cutover.py; scripts/validate_alembic_cutover.py.

**Фактический layer:** Unit/config и component orchestration с заменёнными I/O.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_test_dsn_loader_fails_closed_without_runtime_fallback` | 1 | KEEP | Только CAREEROPS_TEST_POSTGRES_DSN; production DSN не подставляется при отсутствии test DSN. |
| `test_disposable_dsn_guard_accepts_explicit_local_test_targets` [3 параметра]<br>`test_disposable_dsn_guard_rejects_remote_or_unsafe_targets` [10 параметра] | 13 | KEEP | 3 допустимых и 10 недопустимых DSN, включая remote hostaddr при localhost host: разные destructive-target границы. Сохранить все параметры, назвать их короткими смысловыми ids. |
| `test_catalog_normalization_is_order_independent_and_oid_free`<br>`test_catalog_fingerprint_changes_only_with_meaningful_catalog_state` | 2 | KEEP | Стабильная нормализация/хеш каталога и чувствительность к изменённому состоянию. Первый тест реально доказывает перестановку строк/нормализацию значений; исключение OID обеспечивается также SQL capture, не одним названием теста. |
| `test_alembic_config_is_pinned_to_validated_test_database` | 1 | KEEP | Программно проверенный URL остаётся authoritative, сторонний runtime DSN не перенаправляет migration validation. |
| `test_live_schema_accepts_future_v2_table_declared_by_metadata` | 1 | KEEP | Текущий validator принимает расширенный target_metadata. Это регрессия исправления CAR-46, а не тест несуществующего V2-компонента; KEEP сегодня. |
| `test_manual_fresh_path_uses_dynamic_graph_head`<br>`test_manual_legacy_path_stamps_baseline_then_upgrades_to_dynamic_head`<br>`test_manual_report_marks_post_stamp_upgrade_noop_without_descendants` | 3 | KEEP | Оркестратор выбирает graph head; legacy сначала stamp baseline, затем только descendants; no-op заявляется лишь при отсутствии descendants. Mocks здесь проверяют команды управляющего сценария, реальный DDL доказывается отдельным suite. |

**Duplicated infrastructure / payloads:** Малые _catalog/_schema_summary нужны только этому модулю; shared fixture не оправдан. Большой legacy orchestration test (89 строк) смешивает control flow и report text.

**Missing current regressions:** R05, R24–R28.

**Proposed action / проблемные проверки:** Оставить DSN cases и current CAR-46 regression. В дальнейшем отделить safety/normalization/reporting от реальных fresh/legacy paths; не превращать ожидаемый current head в константу.

### 5.3. test_alembic_migrations.py

**Domain:** Revision graph governance. **Production:** alembic/versions/*; Alembic ScriptDirectory.

**Фактический layer:** Fast contract без БД.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_alembic_revision_graph_has_one_root_one_reachable_head` | 1 | KEEP | Один root, один достижимый head, корректные ancestry/links. Миграционный graph — контракт; head не должен быть навсегда равен baseline. |

**Duplicated infrastructure / payloads:** _config имеет доказанный reuse с baseline, но настолько мал, что общий helper необязателен.

**Missing current regressions:** R28: достижимый graph не доказывает неизменность исторических файлов.

**Proposed action / проблемные проверки:** Сделать единственным владельцем root/head/ancestry assertions, приняв MERGE из baseline.

### 5.4. test_alembic_postgres_integration.py

**Domain:** Реальный DDL и schema drift. **Production:** alembic/env.py; baseline revision; src/careerops_storage/alembic_cutover.py; schema.py; sql/migrations/0001–0005.

**Фактический layer:** Настоящая integration_postgres.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_fresh_database_reaches_head_matches_metadata_and_is_idempotent` | 1 | KEEP | Реальный fresh upgrade до graph head, пустой metadata diff и повторный upgrade без изменения каталога. |
| `test_legacy_stamp_preserves_schema_then_applies_only_descendants` | 1 | KEEP | SQL 0001–0005 → stamp baseline без изменения каталога → актуальные descendants. Защищает DDL/cutover, но не сохранность данных: populated legacy rows не используются. |
| `test_metadata_drift_detector_reports_no_diff_at_head` | 1 | DELETE_NOW | Точная подмножина fresh-test: тот же _upgrade_to_graph_head и compare_live_schema_to_metadata == (). Дополнительной mutation, fixture boundary или состояния нет; safe DELETE_NOW-кандидат. |
| `test_metadata_drift_detector_reports_controlled_extra_column` | 1 | KEEP | Сначала clean diff, затем реальный ALTER TABLE и обнаружение extra column. Положительный сигнал detector обязателен; его нельзя удалить вместе с повторным clean-only тестом. |
| `test_alembic_created_database_can_round_trip_through_base` | 1 | KEEP | Fresh Alembic head → base с удалением managed schema → head. Не разрешает downgrade stamped legacy database. |

**Duplicated infrastructure / payloads:** Fixture disposable_postgres_target использует production validator и scoped schema reset; второй PG module имеет другой, более опасный DSN guard и DROP DATABASE.

**Missing current regressions:** R05, R24–R28.

**Proposed action / проблемные проверки:** Сохранить fresh, populated legacy, positive drift и fresh-only downgrade как разные contracts. Один clean-only duplicate удалить после переноса в cleanup Feature. Единый безопасный test-target policy нужен до расширения DB suite.

### 5.5. test_backfill_hh_postgres.py

**Domain:** ETL backfill selection и failure isolation. **Production:** scripts/backfill_hh_postgres.py; src/careerops_etl/hh_s3_to_postgres.py; src/careerops_storage/postgres.py.

**Фактический layer:** Component orchestration: настоящий backfill, fake loader/connection.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_each_batch_has_an_independent_transaction` | 1 | KEEP | У каждого run свой transaction scope; ошибка одного отражена в отчёте, следующий загружается. Event fake доказывает управление scope, не физический rollback PostgreSQL. |
| `test_limit_selects_only_earliest_batches`<br>`test_run_id_selects_exactly_one_requested_batch` | 2 | KEEP | Bounded selection по порядку discovery и точный выбор requested UUID — два разных operational режима. |
| `test_run_id_and_limit_are_rejected_by_runtime_validation`<br>`test_run_id_and_limit_are_mutually_exclusive_cli_options` | 2 | KEEP | Mutual exclusion защищена на Python API и argparse boundary. Это разные пути обхода, не семантический дубль. |

**Duplicated infrastructure / payloads:** FakeTransaction/FakeConnection повторяют event recorder в materializer и postgres_storage. Это не движок transaction semantics.

**Missing current regressions:** R06–R07, R09.

**Proposed action / проблемные проверки:** Оставить API/CLI selection checks, выделить scoped recorder только после общего reuse. Реальную rollback/replay проверку проводить через load_hh_run_transactionally.

### 5.6. test_hh_account_orchestration.py

**Domain:** Multi-resume account APPLY и shared quota. **Production:** src/careerops_integrations/hh/batch_cli.py; apply_batch.py; resume_sync.py; configuration.py.

**Фактический layer:** Оба tests — fast component, несмотря на test_integration_* в имени.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_integration_account_apply_without_scheduler_quota_fails_closed` | 1 | MOVE | Account APPLY требует scheduler allocation до запуска binding. Перенести в component/hh/account_apply; слово integration в имени не означает реальный PostgreSQL/HH. |
| `test_integration_account_apply_runs_all_bindings_with_one_scheduler_quota` | 1 | MOVE | Две bindings делят одну квоту 3: первый child расходует 2, второй получает остаток 1; сохраняются profile/binding metadata. Child полностью fake, поэтому фактические POST и учёт ambiguous attempts этим не проверены. |

**Duplicated infrastructure / payloads:** Ref/FakeStore совпадают по роли с observe/application audit/raw. _accounts и _reconciliation создают большой synthetic binding inventory; некоторые формы отличаются от observe fixtures.

**Missing current regressions:** R01–R03, R13, R19.

**Proposed action / проблемные проверки:** Перенести в component/hh/account_apply, не в integration_postgres. Сохранить child allocation assertions, добавить позднее actual APPLY boundary coverage без живого HH.

### 5.7. test_hh_application_audit.py

**Domain:** External application safety, evidence и claims. **Production:** src/careerops_integrations/hh/application_audit.py; application_claims.py; runtime.py; driver.py; test_bridge.py.

**Фактический layer:** Component service с fake ports; не реальная DB atomicity.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_audited_application_persists_four_objects_and_exact_evidence` | 1 | KEEP | Перед/после vacancy, request/result и точное resume-specific evidence образуют replayable application audit; чистота исходного body и отдельные локальные поля — реальный контракт формата. |
| `test_test_vacancy_uses_upstream_test_executor` | 1 | KEEP | has_test направляет отправку в upstream executor. Это отдельный внешний transport route, а не дубль lexical-filter acceptance. |
| `test_structural_guards_block_application` [3 параметра] | 3 | KEEP | Archived, closed и external response URL блокируются перед отправкой. В будущем усилить zero-POST assertion; три параметра оставить. |
| `test_global_relation_for_first_resume_does_not_block_second_resume`<br>`test_existing_exact_resume_vacancy_pair_is_blocked_without_post` | 2 | KEEP | Глобальный HH relations не запрещает другое resume; точное уже существующее evidence для пары запрещает POST. Противоположные стороны одной identity boundary, обе нужны. |
| `test_persistent_claim_blocks_sequential_duplicate`<br>`test_atomic_claim_allows_only_one_concurrent_post` | 2 | KEEP | Service уважает отказ acquisition при последовательных/конкурентных вызовах. In-memory Lock проверяет интеграцию service с claim port; реальную атомарность PostgreSQL этот fake не доказывает. |
| `test_apply_materializes_missing_vacancy_before_claim_and_posts_once`<br>`test_same_vacancy_has_one_materialization_and_resume_specific_claims` | 2 | KEEP | До acquire подготовлена отсутствующая canonical vacancy; два resume получают независимые claims при единой vacancy. FK preparation и pair fan-out — разные регрессии. |
| `test_account_label_rename_does_not_create_a_new_claim_identity` | 1 | KEEP | Переименование account label не создаёт новую application identity и не снимает duplicate protection. |
| `test_ambiguous_post_is_uncertain_and_never_blindly_retried`<br>`test_uncertain_precheck_blocks_post_and_persists_uncertain_claim` | 2 | KEEP | Неопределённый результат после POST и неопределённость до POST имеют разные write-attempt последствия; в обоих случаях блокируется слепой retry. Сохранить оба сценария. |
| `test_pre_fetched_before_avoids_duplicate_initial_fetch` | 1 | KEEP | Переданный свежий before snapshot устраняет дополнительный HH GET; остаётся after fetch. Это технический request-budget контракт, обоснованный счётчик внешних вызовов. |
| `test_claim_timestamp_is_timezone_aware` | 1 | MERGE | Тот же successful lifecycle повторён ради единственного claimed_at.tzinfo is UTC. Перенести этот assertion в audited success; без переноса удалять нельзя. |

**Duplicated infrastructure / payloads:** 244 строки до первого test; MemoryClaimStore 87 строк повторяет ownership/state machine, FakeDriver 72 строки. Writer fake входит в группу D1, vacancy builder — D3.

**Missing current regressions:** R01–R03, R13, R17, R23, R29.

**Proposed action / проблемные проверки:** SPLIT модуля по guards/claims/audit-confirmation. KEEP tests могут менять расположение без смены verdict. Сохранить adversarial fakes для service boundary, перенести доказательство SQL в real DB suite.

### 5.8. test_hh_configuration.py

**Domain:** HH accounts, query catalog и scheduler policy configuration. **Production:** src/careerops_integrations/hh/configuration.py; config/hh_accounts*.toml; config/hh_discovery.toml.

**Фактический layer:** Unit validation + filesystem/TOML component smoke.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_committed_catalog_and_n_account_n_binding_topology_load` | 1 | REWRITE | Smoke реальных TOML полезен; жёсткие 3 аккаунта, 20 query sets, 388 queries и operator limits не являются вечной семантикой loader. Отделить config smoke от синтетических N-account/N-binding сценариев. |
| `test_catalog_contains_required_broad_ru_en_families` | 1 | REWRITE | Broad RU/EN discovery — актуальная policy. Проверять включённые effective queries, не только наличие текстов в каталоге; отключение нужных queries не должно оставаться невидимым. |
| `test_example_has_only_placeholders_and_no_credential_fields` | 1 | REWRITE | Пример безопасен для копирования: placeholders, auto_apply off, отсутствие credential полей. Substring-поиск token/cookie по всему TOML хрупок к комментариям; проверять разобранные данные и поля. |
| `test_duplicate_query_set_reference_executes_once_per_account_union`<br>`test_disabled_accounts_and_bindings_are_ignored` | 2 | KEEP | Query sets объединяются без повторного исполнения; отключённые accounts/bindings исключаются из effective configuration. |
| `test_duplicate_keys_are_rejected` [3 параметра] | 3 | KEEP | Три разные коллизии: account key, binding/resume key, source profile. Не сокращать параметризацию как якобы одно и то же исключение. |
| `test_unknown_query_set_and_malformed_toml_are_rejected` | 1 | SPLIT | Неизвестная domain reference и синтаксически сломанный TOML — независимые failures; разделить диагностику на два case. |
| `test_credentials_are_not_supported_by_strict_schema`<br>`test_apply_schedule_must_have_capacity_to_reach_daily_cap` | 2 | KEEP | Loader отвергает credential fields; APPLY schedule обязан иметь ёмкость для daily cap. Не дублирует проверку безопасного committed example или успешного планирования. |

**Duplicated infrastructure / payloads:** Большие inline TOML в parametrization порождают огромные node IDs; _write_minimal_discovery локален. Committed catalog counts — oracle операционной настройки, не fixture reuse.

**Missing current regressions:** R13 для env capability boundary; новые role/scoring cases относятся к FUTURE_CONTRACTS.

**Proposed action / проблемные проверки:** Разделить loader syntax/schema, synthetic topology и smoke committed configuration. Использовать короткие ids параметров; не сокращать три duplicate-key cases.

### 5.9. test_hh_cover_letters.py

**Domain:** Vacancy-specific factual cover letter. **Production:** src/careerops_integrations/hh/cover_letters.py.

**Фактический layer:** Чистые unit tests.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_cover_letter_contains_vacancy_and_company` | 1 | KEEP | Письмо адресуется нужной вакансии/компании, не generic шаблон с потерянной identity. |
| `test_cover_letter_uses_only_real_skill_intersection` | 1 | KEEP | Нельзя приписывать кандидату навыки без пересечения с фактическим resume evidence. |
| `test_domain_changes_focus` | 1 | KEEP | Релевантный domain меняет содержательный акцент письма. V2 scoring сам по себе не отменяет этот контракт. |

**Duplicated infrastructure / payloads:** Небольшой _vacancy повторяет минимальный HH full payload, D3; общий полный HH мегабилдер не нужен.

**Missing current regressions:** R30.

**Proposed action / проблемные проверки:** Оставить factual intersection и domain variation. Нет доказательства, что текущие письма obsolete либо обязательно заменяются V2.

### 5.10. test_hh_filtering.py

**Domain:** Legacy APPLY title/description filtering, user exclusions и routing safety. **Production:** src/careerops_integrations/hh/filtering.py; apply_batch.py.

**Фактический layer:** Unit policy; эти функции не являются high-recall OBSERVE pipeline.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_accepts_ml_title`<br>`test_accepts_senior_data_scientist_any_experience`<br>`test_accepts_leading_grade_data_scientist`<br>`test_accepts_senior_ml_engineer`<br>`test_accepts_senior_cv_engineer`<br>`test_accepts_middle_ai_engineer`<br>`test_accepts_mlops_devops`<br>`test_accepts_mlops_center_devops`<br>`test_accepts_python_ai_agents_developer`<br>`test_accepts_cv_specialist` | 10 | RETIRE_AFTER_V2 | 10 lexical acceptance примеров действующего APPLY gate: senior/grade, ML/DS/CV/MLOps и AI engineering. Сохранить до замены gate; не переносить эти precision-решения как вечные правила high-recall DISCOVERY. |
| `test_rejects_team_lead`<br>`test_rejects_tech_lead`<br>`test_rejects_plain_lead_data_scientist`<br>`test_rejects_timlead`<br>`test_rejects_product_manager_cv`<br>`test_rejects_product_owner_mlops`<br>`test_rejects_system_analyst_mlops`<br>`test_rejects_ai_content_specialist`<br>`test_rejects_ai_site_specialist`<br>`test_rejects_ai_ux_ui`<br>`test_rejects_plain_devops_ai`<br>`test_rejects_devops_genai_without_mlops`<br>`test_rejects_ios_ai`<br>`test_rejects_csharp_ml`<br>`test_rejects_unity_ai`<br>`test_rejects_quality_control_cv`<br>`test_rejects_generic_ai_specialist_without_engineering_role` | 17 | RETIRE_AFTER_V2 | 17 исключений management/нерелевантных ролей в текущем title gate. RU/EN и лексические контексты различаются: table-driven rewrite может убрать boilerplate, но не 17 регрессионных примеров. Retire только после замены соответствующего gate. |
| `test_prefilter_rejects_teamlead_without_full_fetch`<br>`test_prefilter_accepts_senior_ml_without_experience_check` | 2 | RETIRE_AFTER_V2 | Два решения раннего APPLY prefilter до full fetch. Не дубль full-validation boundary; временно сохранить и связать retirement с переходом на high-recall + durable fetch. |
| `test_rejects_drones_in_title_prefilter`<br>`test_rejects_donetsk_in_title_prefilter`<br>`test_rejects_military_context_in_description`<br>`test_rejects_drone_context_in_description`<br>`test_rejects_relocation_context_in_description`<br>`test_rejects_rotational_shift_context` | 6 | KEEP | 6 явно заданных пользовательских exclusions: UAV, география, military, relocation/вахта. Сохранить защиту policy; в V2 место её применения пересмотреть отдельно, не теряя discovered RAW/backlog. Это не разрешение запрещать сохранение source. |
| `test_accepts_hh_test_for_upstream_executor`<br>`test_external_response_still_blocks_after_full_fetch` | 2 | KEEP | HH test разрешает поддерживаемый executor; внешний response URL не отправляется неподдерживаемым способом. Эти safety/routing условия переживают замену matching. |
| `test_prefilter_treats_global_relations_as_non_resume_specific`<br>`test_full_validation_treats_global_relations_as_non_resume_specific` | 2 | KEEP | И ранний prefilter, и полный validator не используют vacancy-global relations как доказательство отклика конкретного resume. Оба независимых пути способны внести регрессию. |

**Duplicated infrastructure / payloads:** 39 коротких functions, 226 строк; local vacancy builder D3. Много одинакового arrange, но небольших и семантически разных RU/EN словарных примеров.

**Missing current regressions:** Дополнительного доказанного отдельного current regression family сверх общих APPLY guards здесь не выделено; high-recall/requirement scoring — F03/F07/F08.

**Proposed action / проблемные проверки:** Не удалять модуль сейчас. Поместить 29 временных gate examples в явно обозначенную legacy policy область; 10 surviving safety/user-policy cases сохранить. Parameterization уменьшает boilerplate, не количество необходимых inputs.

### 5.11. test_hh_mapper.py

**Domain:** Source → canonical/operational mapping. **Production:** src/careerops_integrations/hh/mapper.py; models.py; src/careerops_contracts/vacancy.py; source.py.

**Фактический layer:** Unit mapping, две ответственности в одном case.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_maps_realistic_hh_vacancy` | 1 | SPLIT | Один case объединяет canonical vacancy mapping/RAW lineage и operational extraction HH flags/relations. Разнести две ответственности; fixture синтетический realistic payload, не подтверждённый production capture. |

**Duplicated infrastructure / payloads:** Один inline realistic dictionary; не source capture. Разделение payload на fixture полезно лишь при reuse или нескольких mutation cases.

**Missing current regressions:** R22.

**Proposed action / проблемные проверки:** SPLIT canonical mapping и operational flags; не удалять already_interacted mapping только потому, что relations нельзя использовать как resume-specific authorization.

### 5.12. test_hh_observe.py

**Domain:** Broad discovery, RAW-v3, provenance, resume matrix и cursor. **Production:** src/careerops_integrations/hh/observe.py; driver.py; configuration.py; resume_sync.py; src/careerops_storage/postgres.py.

**Фактический layer:** Component orchestration с fake HH/S3/cursor и отключённым sleep.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_observe_unions_deduplicates_and_full_fetches_without_filtering` | 1 | SPLIT | В одном case: union/provenance, dedup first representative, full fetch даже legacy-rejected title, отсутствие APPLY. Разделить локализуемые проверки, сохранив один orchestration smoke. |
| `test_observe_preserves_source_payload_purity_and_raw_pages` | 1 | KEEP | Exact page/search item/full vacancy bodies не получают collected_at/local policy; provenance живёт в sidecars. Сохранить RAW-v3 формат и совместимость архивов. |
| `test_observe_keeps_successful_earlier_page_when_later_page_fails` | 1 | KEEP | Ошибка следующей search page не уничтожает уже сохранённую страницу и её vacancies. Это текущая частичная надёжность, ещё не lossless PostgreSQL backlog. |
| `test_observe_persists_independent_vacancy_resume_evaluation_pairs` | 1 | KEEP | Вся матрица real active bindings создаётся независимо от provenance overlap; нет вымышленного owner resume. В RAW duplicate_key содержит lineage label, operational claim identity остаётся canonical pair. |
| `test_same_vacancy_is_not_deduplicated_across_account_runs` | 1 | KEEP | Один vacancy ID в разных account runs сохраняет независимый audit контекст. Нельзя заменить dedup глобальным seen-set. |
| `test_observe_enforces_unique_and_full_fetch_technical_bounds` | 1 | RETIRE_AFTER_V2 | 3 discovered → 2 candidate sidecars → 1 full fetch; технически ограниченный candidate не получает evaluation. RAW pages/discovery не стираются, но это ограничение сегодняшней обработки, а не будущий lossless backlog контракт. После V2 заменить ожидание отсутствия deferred work; сами ограничения числа внешних запросов сохранить в queue/worker tests. |
| `test_observe_rotates_a_bounded_query_window_across_runs` | 1 | KEEP | Последовательные окна каталога обходят все queries и wrap без голодания. Fake cursor доказывает wiring/порядок внешнего поиска; PG concurrency/reset требуют отдельного доказательства. |

**Duplicated infrastructure / payloads:** FakeStore D1; MemoryQueryCursorStore 44 строки повторяет reservation алгоритм. _discovery/_account/_reconciliation/_pages и inline bindings занимают большую часть 708 строк; два tests длиннее 80 строк.

**Missing current regressions:** R18, R29; lossless backlog/fetch queue отсутствуют по roadmap: F01–F03, не current-failing tests.

**Proposed action / проблемные проверки:** SPLIT discovery/pages, purity/provenance, vacancy×resume и query rotation; один smoke соединяет workflow. Sidecars pending_filtering_v2 — текущий archived RAW-v3 envelope, compatibility decoder сохраняется после V2.

### 5.13. test_hh_raw.py

**Domain:** Local RAW и S3 producer purity. **Production:** src/careerops_integrations/hh/raw.py; batch_cli.py; apply_batch.py; observe.py; src/careerops_storage/s3.py.

**Фактический layer:** Сейчас mocked filesystem unit + generic wrapper check; target — filesystem component и producer contract.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_local_raw_store` | 1 | REWRITE | Глобальные monkeypatch Path.exists/mkdir/write_bytes исключают реальную collision/immutability ветку. Использовать временный каталог и проверить bytes/hash + повторный same/different payload; local dev/bootstrap path пока поддерживается. |
| `test_s3_raw_writer_keeps_source_body_pure` | 1 | REWRITE | Вызывает batch_cli._write_json на искусственном source body, хотя текущие source writers находятся также в observe/apply_batch. Проверять реальный producer boundary и metadata; не считать generic wrapper достаточным RAW regression. |

**Duplicated infrastructure / payloads:** Ref/FakeStore D1. Глобальный monkeypatch класса Path скрывает реальные I/O ветки и может затронуть соседний код в scope теста.

**Missing current regressions:** R04, R10, R29.

**Proposed action / проблемные проверки:** Проверять real temporary files и current source writers. Существующий local RAW путь не объявлять obsolete: он используется CLI/bootstrap.

### 5.14. test_hh_resume_sync.py

**Domain:** Authoritative inventory, lifecycle/binding и selection. **Production:** src/careerops_integrations/hh/resume_sync.py; apply_batch.py; driver.py; src/careerops_storage/postgres.py.

**Фактический layer:** Unit reconciliation + JSON filesystem component; четыре apply eligibility cases относятся к другому domain.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_apply_rejects_every_non_published_upstream_resume` [3 параметра]<br>`test_apply_accepts_explicitly_published_upstream_resume` | 4 | MOVE | 3 non-published статуса и published success проверяют apply_batch eligibility. Перенести из resume reconciliation в unit/hh/apply_eligibility, сохранив все 4 cases. |
| `test_existing_resume_remains_active`<br>`test_title_and_content_change_keep_identity_and_binding` | 2 | KEEP | Стабильная upstream identity, first_seen/history и binding при обновлении содержимого; last_seen обновляется. Title не является ключом. |
| `test_new_resume_is_registered_unassigned_and_not_auto_applied` | 1 | KEEP | Найденный новый resume появляется в inventory, но не получает binding/auto_apply автоматически. |
| `test_missing_known_resume_is_marked_deleted_and_history_is_retained` | 1 | KEEP | Полный успешный inventory без прежнего ID помечает его deleted, сохраняет first_seen и историю binding. |
| `test_deleted_resume_is_not_selected_for_new_applications` | 1 | MERGE | Повтор того же missing→deleted setup. Перенести явные assertions отсутствия в evaluation_resumes/auto_apply_resumes в lifecycle test; boolean flags сами по себе эти projection assertions не заменяют. |
| `test_new_id_with_same_title_does_not_inherit_deleted_binding`<br>`test_multiple_active_resumes_remain_independently_selectable`<br>`test_duplicate_identity_is_resume_plus_vacancy_not_global_vacancy` | 3 | KEEP | Новый ID не наследует binding по title; разные active resumes независимы; application duplicate key включает resume. Разные регрессионные причины, не кандидаты DELETE. |
| `test_reconciliation_is_idempotent`<br>`test_transport_failure_aborts_without_marking_resumes_deleted` | 2 | KEEP | Повтор inventory не создаёт новых регистраций; транспортная ошибка не интерпретируется как пустой authoritative inventory. |
| `test_json_registry_persists_deleted_history_for_future_runs` | 1 | KEEP | Явный JSON bootstrap/dev registry сохраняет удалённые resumes между запусками. V2 не утверждает удаление этого режима; RETIRE не обоснован. |
| `test_published_unpublished_published_preserves_binding_and_apply_selection` | 1 | KEEP | Lifecycle resume остаётся active при публикационных переходах, binding сохраняется, APPLY eligibility отключается/возвращается. |

**Duplicated infrastructure / payloads:** MemoryRegistry/FakeDriver малы; _account/_resume/_sync локализуют setup. Shared account builder вводить только после выравнивания используемой domain формы, не по одинаковому имени.

**Missing current regressions:** R14–R15.

**Proposed action / проблемные проверки:** MOVE 4 eligibility cases, MERGE duplicate deletion scenario с сохранением selection assertions. Оставить JSON history и опубликованность независимо от identity; PG lifecycle проверить реальным round trip.

### 5.15. test_hh_runtime.py

**Domain:** Mode/capability, upstream adapter и CLI compatibility. **Production:** src/careerops_integrations/hh/runtime.py; driver.py; test_bridge.py; batch_cli.py; application_cli.py.

**Фактический layer:** Смесь unit policy, adapter contract и service component.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_exactly_two_modes_and_default_observe`<br>`test_batch_cli_defaults_to_postgres_resume_registry`<br>`test_invalid_mode_fails_closed` | 3 | KEEP | Режимы OBSERVE/APPLY, безопасный default, PG registry по умолчанию и отказ неизвестного mode — действующий runtime contract. |
| `test_external_write_requires_both_independent_conditions` [3 параметра]<br>`test_apply_with_explicit_external_opt_in_is_write_capable` | 4 | KEEP | Все 3 запрещённые комбинации mode/flag и разрешённая APPLY+opt-in. Ни одной независимой заслонки не удалять. |
| `test_driver_blocks_normal_and_test_submission_in_observe`<br>`test_direct_test_bridge_also_requires_central_write_capability`<br>`test_application_service_observe_guard_fails_before_any_side_effect` | 3 | KEEP | Driver, direct test bridge и service — разные достижимые write boundaries. Sentinel-проверки отсутствия side effects здесь обоснованы safety-контрактом. |
| `test_driver_delegates_profile_auth_and_session_to_hh_applicant_tool` | 1 | REWRITE | Проверяется private _base_command и точная форма argv. Нужны profile/config-dir/module в реальном subprocess boundary публичного adapter вызова; произвольный порядок аргументов не контракт. |
| `test_driver_exposes_exact_ordered_search_pages_before_dedup` | 1 | SPLIT | Raw ordered page stream и flattened deduplicated search API — два публичных результата. Разделить, сохранив exact page data и порядок внешних запросов. |
| `test_driver_requires_authoritative_resume_items_list`<br>`test_driver_reads_every_resume_inventory_page` | 2 | KEEP | Inventory требует настоящий items list и все страницы; incomplete inventory не должен инициировать массовое deleted. |
| `test_driver_refuses_incomplete_negotiation_pagination_metadata`<br>`test_driver_negotiation_evidence_is_exactly_resume_specific` | 2 | KEEP | Неопределённая полнота negotiations запрещает уверенный вывод; чужой resume не подтверждает эту пару. Вторая проверка преимущественно negative, positive exact evidence на поздней странице отсутствует. |
| `test_deprecated_live_maps_to_apply_but_does_not_create_capability`<br>`test_deprecated_application_live_does_not_hide_invalid_mode` | 2 | KEEP | Поддерживаемый --live alias не выдаёт opt-in и не скрывает invalid --mode. Deprecation не равна obsolete; дорожная карта V2 не обещает удаление CLI alias. |

**Duplicated infrastructure / payloads:** Существенных повторяющихся shared fake classes нет; inline sentinels иногда предпочтительнее общей сложной fixture.

**Missing current regressions:** R13–R14, R23.

**Proposed action / проблемные проверки:** SPLIT модуль runtime guards / driver pagination / CLI adapters. Не сливать independent write barriers ради единственного mock test.

### 5.16. test_hh_s3_to_postgres.py

**Domain:** RAW-v2/v3 decoding, mapping, audit validation и materialization. **Production:** src/careerops_etl/hh_s3_to_postgres.py; src/careerops_storage/postgres.py; s3.py; scripts/backfill_hh_postgres.py.

**Фактический layer:** Component ETL с fake storage/sink; названия двух tests преувеличивают доказанную DB идемпотентность/rollback.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_discovers_batch_and_candidate_objects_from_actual_layout`<br>`test_loads_header_and_normalizes_time_to_utc` | 2 | KEEP | Реальный layout keys превращается в runs/candidates; header нормализует UTC и canonical profile/resume, стартует incomplete. Fake sink проверяет передаваемые domain значения, не SQL. |
| `test_loads_full_batch_and_finalizes_summary` | 1 | SPLIT | Один case объединяет partial/full mapping, decision metadata, application audit crossrefs, summary completion. Разделить эти ответственности и оставить один end-to-end materialization smoke. |
| `test_completed_application_allows_explicitly_failed_after_snapshot` | 1 | KEEP | После попытки POST допустим явно описанный failure vacancy_after; ETL не требует отсутствующий snapshot и не фальсифицирует after_uri. |
| `test_legacy_raw_without_metadata_falls_back_to_last_modified` | 1 | KEEP | ETL использует LastModified только при отсутствии collected_at; S3 adapter отдельно валидирует metadata. Разные границы, оба tests нужны. |
| `test_batch_without_summary_remains_incomplete`<br>`test_failed_batch_outcome_is_not_invented_as_application` | 2 | KEEP | Без summary run не объявляется finished; непроверяемый failed outcome не создаёт фиктивную application row. Второй fixture не содержит фактическое новое поле external_write_attempted, поэтому пропускает R01. |
| `test_rejects_mismatched_path_and_payload_ids` [4 параметра]<br>`test_rejects_malformed_batch_path` | 5 | KEEP | 4 cross-reference mismatches: run ID, vacancy path/body, decision run и application resume; отдельная invalid path syntax. Сохранить все примеры identity corruption. |
| `test_v3_observation_materializes_all_real_resume_pairs_without_fake_owner` | 1 | KEEP | RAW-v3 observation run привязан к account/profile; work items созданы для двух настоящих resumes, а не для fabricated owner. |
| `test_v3_replay_of_same_run_creates_zero_duplicate_oltp_rows` | 1 | REWRITE | IdempotentTransactionalSink сам реализует dict-key UPSERT. Проверка размера его dict доказывает поведение fake; перенести доказательство идемпотентности в actual ETL→PostgreSQL. |
| `test_v3_mid_materialization_failure_rolls_back_the_whole_run` | 1 | REWRITE | SnapshotTransaction сам восстанавливает deepcopy fake state. Полезная fault-injection идея; физический rollback actual loader должен подтверждаться реальной транзакцией. |
| `test_v3_observation_safety_fields_are_not_weakly_accepted` | 1 | KEEP | Числовой submitted=1 не принимается как OBSERVE zero literal. Не считать один mutation покрытием всех identity/fan-out/auto_apply/provenance проверок v3. |

**Duplicated infrastructure / payloads:** 942 строки; FakeSink 53, IdempotentTransactionalSink 86, SnapshotTransaction 23. _complete_store 87, v3 run 50, summary 30, observation 45, evaluations 70 строк. Это версии synthetic wire fixtures, их expected values должны оставаться независимыми от producer.

**Missing current regressions:** R01, R06–R07, R11–R12; текущая incomplete/recovery граница R08.

**Proposed action / проблемные проверки:** SPLIT по discovery/decoding, v2 audited APPLY, v3 observations, actual DB materialization. Убрать имитацию UPSERT/rollback только после подключения настоящего loader к PostgreSQL.

### 5.17. test_materialize_hh_pending.py

**Domain:** Периодический подбор completed OBSERVE RAW. **Production:** scripts/materialize_hh_pending.py; src/careerops_etl/hh_s3_to_postgres.py.

**Фактический layer:** Component orchestration с fake loader; один success-selection сценарий.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_materializer_loads_only_pending_finished_observe` | 1 | KEEP | Отбираются completed RAW-v3 OBSERVE, пропускаются уже имеющийся ID и APPLY-v2, вызывается transaction. No-summary run и incomplete row в PG fixture отсутствуют; production bug R08 остаётся невидимым. |

**Duplicated infrastructure / payloads:** FakeTransaction D2; FakeCursor возвращает только ID без статуса и принимает любой SELECT с именем таблицы.

**Missing current regressions:** R06–R09.

**Proposed action / проблемные проверки:** Не удалять небольшой модуль. Явно различить RAW completed и PG finished; retry incomplete после появления summary — текущая recovery задача.

### 5.18. test_postgres_integration.py

**Domain:** Real psycopg claims, current-state writes, cursor и transactions. **Production:** src/careerops_storage/postgres.py; src/careerops_integrations/hh/application_audit.py; sql/migrations/0001–0005.

**Фактический layer:** Настоящая DB integration; ETL control flow частично скопирован в fixture.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_migration_chain_creates_all_orchestration_relations` | 1 | MERGE | Реальный to_regclass presence после SQL 0001–0005. Перенести список relations в legacy-stamp test/legacy setup assertion; это та же schema и слабее полного catalog/head contract. |
| `test_apply_materializes_vacancy_before_resume_specific_claim` | 1 | KEEP | Реальная vacancy подготовлена до FK claim, rename не даёт duplicate, второе resume разрешено. Fake HH driver проверяет TransactionStatus.IDLE при внешних вызовах — критическая защита от сети внутри PG transaction. |
| `test_claim_retry_and_non_retryable_states`<br>`test_concurrent_claim_has_exactly_one_winner` | 2 | KEEP | Реальный PG допускает retry FAILED_SAFE_TO_RETRY, блокирует SUBMITTING/SUBMITTED/UNCERTAIN и даёт одного winner двум соединениям. Не заменять MemoryClaimStore; усилить контролируемым overlap при необходимости. |
| `test_observe_replay_and_complete_transaction_rollback` | 1 | REWRITE | База реальная, но _materialize_observe_run повторяет production алгоритм на 100 строк и не вызывает load_hh_batch. Сохранить полезные SQL effects, переподключить сценарий к actual transactional ETL. |
| `test_observe_query_cursor_reservation_uses_real_psycopg` | 1 | KEEP | Реальный placeholder/modulo SQL и два последовательных окна. Это regression fix 77b902b, где literal % потребовал %% для psycopg; substring unit этого не заменяет. |

**Duplicated infrastructure / payloads:** Dangerous fixed-name DB fixture; _materialize_observe_run 100 строк дублирует алгоритм. _AuditStore D1 и _vacancy D3; _ApplicationDriver намеренно проверяет TransactionStatus.IDLE.

**Missing current regressions:** R05–R07, R15–R18, R24–R27.

**Proposed action / проблемные проверки:** Разделить claims/cursor/ETL/legacy migrations. Runtime tests запускать на Alembic head; legacy chain оставить отдельным migration route. Не терять IDLE assertions при переносе fixtures.

### 5.19. test_postgres_storage.py

**Domain:** Explicit SQL persistence и registry adapters. **Production:** src/careerops_storage/postgres.py; schema.py; sql/migrations/0001–0005.

**Фактический layer:** Преимущественно unit SQL-recording adapter tests; statements не исполняются.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_upsert_source_profile_and_resume_have_idempotent_sql_intent` | 1 | REWRITE | ON CONFLICT substring и параметры не доказывают стабильные IDs/число rows при повторной записи. Проверять два реальных upsert и состояние. |
| `test_partial_vacancy_upsert_only_updates_supported_fields`<br>`test_full_vacancy_upsert_is_newest_observation_wins` | 2 | REWRITE | Обновление только search-supported полей и latest observation wins защищены SQL shape, но не последовательностью старого/нового full/partial payload. Нужны наблюдаемые row values. |
| `test_full_vacancy_rejects_contract_id_mismatch_before_sql` | 1 | KEEP | Canonical vacancy и operational flags с разными IDs отвергаются до SQL. Zero-SQL sentinel — настоящий boundary safety assertion. |
| `test_batch_decision_and_application_sql_match_conflict_keys` | 1 | REWRITE | Три независимых UPSERT identity в одном SQL-recorder test. Переписать по domain outcomes и разделить batch/decision/application без потери conflict-key защиты. |
| `test_reconciled_resume_persists_lifecycle_binding_and_selectability`<br>`test_postgres_resume_registry_rehydrates_primary_runtime_state` | 2 | REWRITE | Сохранение и восстановление resume lifecycle/binding важно; positional tuples/params и WHERE substring не доказывают PG round trip. Проверять объект после save/load и canonical profile identity при смене label. |
| `test_application_claim_migration_uses_canonical_oltp_ids` | 1 | MERGE | Читает frozen SQL 0003 и проверяет FK/UNIQUE strings. Перенести identity assertion в legacy schema + живой pair collision: те же canonical FK/unique уже являются declarative/runtime contracts. Не переписывать 0003. |
| `test_application_claim_acquisition_is_atomic_and_resume_specific`<br>`test_application_claim_transition_fails_on_stale_owner_or_state` | 2 | REWRITE | FakeCursor возвращает заранее выбранные acquired/None; атомарный SQL и stale-owner WHERE реально не исполняются. Нужны DB conflicts, stale UUID/state и отсутствие изменения row. |
| `test_application_claim_fails_closed_when_oltp_identity_is_missing`<br>`test_application_preparation_does_not_invent_missing_resume` | 2 | KEEP | Отсутствующий OLTP resume/vacancy даёт отказ, а не placeholder identity или fabricated resume. Сохранить fast fail-closed boundary checks. |
| `test_application_preparation_requires_resume_then_upserts_vacancy` | 1 | REWRITE | Нужно durable canonical identity до claim; сейчас assertion фиксирует SQL sequence/параметры. Реальный FK/materialization contract уже частично покрыт integration, расширить state assertions вместо копии последовательности. |
| `test_observe_query_window_is_reserved_atomically_by_source_profile` | 1 | REWRITE | Предзаданный cursor row плюс проверка SQL текста не доказывают reservation между конкурентами. Перенести atomicy/reset/wrap в DB integration, оставить только нужный row decoding unit. |
| `test_observation_upserts_use_stable_run_entity_conflict_keys` | 1 | REWRITE | Run/vacancy/resume conflict keys — реальные invariants; текст ON CONFLICT заменить повторным actual materialization с проверкой IDs/rows. |
| `test_insert_columns_are_declared_by_ordered_migrations` | 1 | REWRITE | 117 строк regex-parser legacy SQL и перехвата INSERT. После cutover новые columns идут через Alembic, поэтому parser замораживает неправильный источник schema. Заменить runtime SQL against Alembic head; named-column semantics сохранить. |

**Duplicated infrastructure / payloads:** 802 строки; 5 fake classes, sequenced cursors, tuple rows, _assert_sql_call. D2 recorder и D3 vacancy builder. Самодельный SQL-column parser 117 строк.

**Missing current regressions:** R06–R07, R15–R18, R26–R27.

**Proposed action / проблемные проверки:** 12 REWRITE: сохранять invariant, менять oracle на observed DB state. 3 fail-before-SQL KEEP. SQL legacy-text case MERGE только с переносом historical identity assertion.

### 5.20. test_project_dependencies.py

**Domain:** Runtime/dev dependency boundary. **Production:** pyproject.toml.

**Фактический layer:** Fast packaging contract.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_pytest_asyncio_is_a_dev_only_dependency` | 1 | KEEP | pytest-asyncio остаётся dev dependency и не входит в runtime installation. Маленький полезный packaging contract. |

**Duplicated infrastructure / payloads:** Shared infrastructure отсутствует; extraction не требуется.

**Missing current regressions:** Отдельных доказанных current gaps для этого узкого контракта не выделено.

**Proposed action / проблемные проверки:** MOVE всего файла в unit/packaging возможен как организационная операция; первичный verdict case остаётся KEEP.

### 5.21. test_s3_listing.py

**Domain:** S3 namespace, paging, provenance и checksum adapter. **Production:** src/careerops_storage/s3.py.

**Фактический layer:** Fast adapter component/contract с fake S3 protocol, не SeaweedFS integration.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_iter_keys_uses_pagination_and_returns_relative_keys`<br>`test_full_prefix_is_not_duplicated` | 2 | KEEP | Adapter обходит все object listing pages, возвращает relative keys, не удваивает настроенный prefix. |
| `test_get_json_with_metadata_prefers_collected_at_over_last_modified`<br>`test_get_json_uses_last_modified_for_legacy_metadata` | 2 | KEEP | Восстанавливаются bytes/hash/URI и UTC timestamps; collected_at authoritative, отсутствие metadata явно оставляет fallback ETL. Название legacy test не означает устаревший код. |
| `test_put_json_writes_hash_metadata_and_correct_full_key` | 1 | KEEP | Реальная сериализация adapter отправляет неизменённый JSON body и checksum/collected_at metadata. FakeS3Client проверяет запрос, не гарантии SeaweedFS collision/durability. |
| `test_rejects_invalid_collected_at_metadata` [2 параметра]<br>`test_rejects_bad_sha256_metadata` | 3 | KEEP | Malformed и naive collected_at не маскируются LastModified; корректно оформленный, но неверный SHA256 отвергается. Hash mismatch уже покрыт — не объявлять MISSING повторно. |
| `test_rejects_uri_outside_bucket_or_prefix` | 1 | KEEP | S3 URI другого bucket/prefix отвергается до I/O; сохранить namespace boundary. |

**Duplicated infrastructure / payloads:** AsyncBody/FakePaginator/FakeS3Client имитируют низкоуровневый API и отличаются от D1 JSON stores; объединять их в один fake не следует.

**Missing current regressions:** R04, R10.

**Proposed action / проблемные проверки:** SPLIT модуля по keys/listing, serialization/metadata, client lifecycle, если это упростит владение. Сохранить все 9 cases; реальные object-store semantics требуют отдельной опциональной integration boundary.

### 5.22. test_scheduler_dispatcher.py

**Domain:** Account/day quotas, worker launch, CAPTCHA и durable scheduler state. **Production:** src/careerops_scheduler/dispatcher.py; config.py; src/careerops_integrations/hh/batch_cli.py.

**Фактический layer:** Unit quota/argv + filesystem component dispatcher; не настоящий subprocess/HH integration.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_account_quota_clamps_scheduler_and_per_run_limits` | 1 | MOVE | Проверяет helper из batch_cli, не dispatcher. Перенести в unit/hh/account_apply: allocation = минимум account daily, scheduler remaining, per-run cap; missing quota fail-closed. |
| `test_worker_command_is_explicit_and_never_appends_live_or_resume` | 1 | REWRITE | Worker получает mode/account/settings, не deprecated --live или статический resume. Проверять набор и значения argv options, а не точный tail/order. |
| `test_apply_worker_receives_account_quota_without_static_resume` | 1 | KEEP | Отдельный APPLY launch контракт: передаётся remaining account quota, binding выбирается динамически. Не сливать с OBSERVE launch, теряя параметр режима. |
| `test_apply_state_is_regenerated_when_account_cap_changes` | 1 | KEEP | Изменение cap пересчитывает remaining, уже consumed не обнуляется. Это persistence regression текущего scheduler. |
| `test_captcha_pauses_only_affected_account_and_other_account_runs` | 1 | KEEP | Dispatcher по готовому stopped_on_captcha=true pause-ит один account, второй продолжает. Не доказывает, что actual HH error действительно создаст такой summary. |
| `test_apply_daily_quota_is_enforced_per_account_and_persisted` | 1 | KEEP | Несколько slots делят дневной лимит, остаток переживает чтение state, исчерпанные slots пропускаются. File-backed component, worker fake; guard от реальных uncertain attempts отсутствует. |

**Duplicated infrastructure / payloads:** Большой _plan (44 строки) и parametrized fake worker responses; единственный quota scenario 100 строк. Глобальный scheduler fake framework не нужен.

**Missing current regressions:** R02–R03, R19–R20.

**Proposed action / проблемные проверки:** MOVE HH helper case, REWRITE argv order, SPLIT launch/state/quota/isolation. Сегодня state_dir JSON + lock являются authoritative для scheduler, S3 mirror best-effort; не описывать это как уже реализованную PG queue.

### 5.23. test_scheduler_planner.py

**Domain:** Детерминированные account slots/cadence. **Production:** src/careerops_scheduler/planner.py; config.py; src/careerops_integrations/hh/configuration.py.

**Фактический layer:** Unit planner с чтением committed settings.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_plan_v3_contains_all_accounts_and_configured_observe_runs`<br>`test_apply_plan_has_distinct_cadence_and_can_reach_daily_cap` | 2 | REWRITE | Planner должен следовать переданным account schedules и разделять OBSERVE/APPLY cadence. Привязка к committed 3 accounts и текущим 3/7 runs — хрупкий config snapshot; использовать небольшие synthetic configs. |
| `test_plan_respects_one_global_minimum_gap` | 1 | KEEP | Все слоты соблюдают общий minimum gap и aware timestamps в заданный день. Нужны ещё window/impossible schedule/cache invalidation cases, это не повод удалить существующий. |
| `test_slot_ids_and_actions_are_account_scoped` | 1 | KEEP | Slot IDs/actions account-scoped и однозначно связываются с persistent state. Стабильность identity полезна при restart. |

**Duplicated infrastructure / payloads:** _settings привязан к операционной конфигурации; правильнее малые synthetic account/scheduler objects, а committed TOML оставить в config smoke.

**Missing current regressions:** R21.

**Proposed action / проблемные проверки:** Изолировать scheduling invariants от числа реальных accounts. Внутренний seed здесь допустим для воспроизводимого timetable, но не является FUTURE deterministic matching score.

### 5.24. test_storage_schema.py

**Domain:** Canonical SQLAlchemy Core metadata contract. **Production:** src/careerops_storage/schema.py; src/careerops_storage/__init__.py; frozen baseline/SQL для сверки.

**Фактический layer:** Fast declarative schema contract, не runtime SQL execution и не live drift validation.

| Test case / однородная группа | Cases | Verdict | Защищаемое поведение, ограничение доказательства и действие |
|---|---:|---|---|
| `test_package_exports_one_canonical_metadata_with_current_tables` | 1 | REWRITE | Один exported metadata объект и careerops schema полезны. Точный tuple-order всех columns избыточен для named runtime SQL; expected current table set — явный contract, обновляемый с реальными revisions. Не считать его immutable baseline snapshot. |
| `test_effective_nullability_includes_0005_repairs` | 1 | KEEP | Nullable vacancy title/counters и обязательные audit timestamps после legacy repair — текущие runtime invariants, не formatting DDL. |
| `test_postgresql_specific_types_and_identity_columns` | 1 | KEEP | UUID/JSONB/ARRAY/timestamptz/numeric и generated identities — явный database contract. Не заменять ожидания вычислением из проверяемого metadata. |
| `test_primary_keys_foreign_keys_and_unique_constraints` | 1 | KEEP | Canonical FK и unique pair resume×vacancy защищают целостность/duplicate application; independent expected keys обоснованы. |
| `test_check_constraints_preserve_current_runtime_invariants` | 1 | REWRITE | Преимущественно имена checks и несколько snippets не доказывают выражения/NULL semantics. Добавить actual accept/reject row scenarios и controlled constraint drift, сохранив domain intent. |
| `test_server_defaults_match_effective_postgresql_defaults`<br>`test_explicit_indexes_include_sort_direction_and_partial_predicates` | 2 | KEEP | Server defaults и выбранные PostgreSQL index keys/order/predicates — декларативный schema contract. Нормализованное SQL expression здесь допустимо; это не обещание query planner/performance. |

**Duplicated infrastructure / payloads:** 714 строк: EXPECTED_COLUMNS ~224 и nullable expectations ~86 строк; большая часть — independent oracle. Это не обычные duplicated fixtures, их нельзя получить из проверяемого metadata и объявить проверку сохранённой.

**Missing current regressions:** R24–R28.

**Proposed action / проблемные проверки:** SPLIT identity/constraints, types/defaults/nullability и indexes. Убрать column-order freeze текущей schema, сохранить named semantic expectations. Legacy snapshot должен оставаться явно привязанным к frozen revision.

## 6. Отдельная decision table и доказанный reuse

### 6.1. KEEP / REWRITE / MERGE / DELETE_NOW / RETIRE_AFTER_V2 / SPLIT / MOVE

| Verdict | Collected cases | Основание | Разрешённое следующее действие после CAR-140 |
|---|---:|---|---|
| KEEP | **141** | Актуальный behavior/contract и достаточная ценность существующей проверки | Сохранить inputs и assertions; можно переместить case в подходящий domain |
| REWRITE | **30** | Invariant полезен, но SQL/internal-call/fixture oracle слаб или замораживает неправильный контракт | Сначала доказать новый behavioral oracle, затем заменить прежний; сохранить regression intent |
| MERGE | **5** | Повторяется scenario/setup или часть contract; есть определённый surviving owner | Перенести уникальные assertions, проверить owner и только потом удалить donor |
| DELETE_NOW | **1** | Exact duplicate clean-metadata DB path без самостоятельного поведения | Можно удалить в cleanup task; в CAR-140 файл не меняется |
| RETIRE_AFTER_V2 | **30** | Текущий временный gate/inline processing policy | Продолжать выполнять до соответствующего Feature cutover; retirement отдельно от historical RAW compatibility |
| SPLIT | **5** | Один collected case смешивает независимые responsibilities | Разделить cases, сохранить smoke и все входные сценарии; collected count вправе вырасти |
| MOVE | **7** | Полезный case находится в другом domain/layer | Только relocations/ясное naming; component не становится integration по названию |
| **Итого** | **219** | Mutually exclusive primary decision по каждому collected item | MISSING/FUTURE не входят в этот знаменатель |

REWRITE имеет приоритет над дополнительным SPLIT/MOVE для одного case. Например, SQL test трёх UPSERT responsibilities посчитан один раз как REWRITE; его разделение — дополнительное действие. Шесть oversized modules требуют организационного SPLIT независимо от пяти case-level SPLIT.

### 6.2. Конкретные кандидаты DELETE/MERGE

| Donor | Verdict | Surviving owner / обязательное условие |
|---|---|---|
| `test_alembic_postgres_integration::test_metadata_drift_detector_reports_no_diff_at_head` | DELETE_NOW | `test_fresh_database_reaches_head_matches_metadata_and_is_idempotent` делает тот же upgrade и empty diff (строки 62–74); positive controlled-extra-column test тоже начинает с clean diff. Ничего уникального не переносится |
| `test_alembic_baseline::test_canonical_baseline_remains_a_graph_root` | MERGE | `test_alembic_migrations::test_alembic_revision_graph_has_one_root_one_reachable_head`; сохранить baseline root identity/down_revision и intentional branch-label expectations |
| `test_hh_application_audit::test_claim_timestamp_is_timezone_aware` | MERGE | Successful audited application scenario; добавить туда явный assertion UTC claim timestamp. Это перенос полезного assertion, не признание UTC ненужным |
| `test_hh_resume_sync::test_deleted_resume_is_not_selected_for_new_applications` | MERGE | Missing-known→deleted lifecycle scenario; перенести обе selection projection assertions, не ограничиваться flags |
| `test_postgres_integration::test_migration_chain_creates_all_orchestration_relations` | MERGE | Legacy SQL→stamp integration owner; сохранить explicit required relations/actual catalog evidence и historical setup |
| `test_postgres_storage::test_application_claim_migration_uses_canonical_oltp_ids` | MERGE | Legacy schema identity assertion + actual canonical pair/FK DB behavior и frozen-history guard R28. Нельзя удалить только потому, что current metadata выглядит похоже |

Здесь **1 безусловно доказанный duplicate и 5 условных consolidation candidates**. Объединение scenario не разрешает потерять уникальный failure signal. Для остальных tests достаточного доказательства DELETE_NOW не найдено.

Не являются redundancy:

| Похожие tests | Почему оставить |
|---|---|
| Central mode guard / driver / direct test bridge / service guard | Независимые достижимые write boundaries; обход одного из них не должен давать POST |
| `has_test` acceptance / service executor selection | Одно разрешает routing, другое выбирает actual transport method |
| Global relations в prefilter / full validation / exact application evidence | Разные stages могут ошибочно вводить vacancy-global duplicate lock |
| Precheck uncertainty / post-submit uncertainty | Разный факт внешней попытки и quota accounting |
| Memory concurrent service test / real PG concurrent claim | Первый доказывает реакцию service на port, второй — реальную DB атомарность |
| RAW source purity / S3 metadata encoding / ETL timestamp selection | Source body, transport metadata и consumer provenance — разные contracts |
| Fresh migration / legacy stamp / fresh downgrade | Разные начальные DB states, возможности разрушения данных и migration entry points |
| Clean schema diff / controlled actual drift | Нужны отрицательный и положительный signal detector; удаляется только повторный clean-only path |
| 39 filtering examples | Большая часть — разные лексические входы или разные границы; одинаковая строка assert не доказывает семантический дубль |
| API mutual exclusion / argparse mutual exclusion | Python API можно вызвать в обход CLI |

### 6.3. Duplicated fixtures/builders/fakes: измеримые семейства

В этом аудите «избыточная копия» = число реализаций одной подтверждённой повторяемой роли сверх одной: сумма `N_family - 1`. Это ручной semantic inventory, а не число всех классов с именем Fake. Количество classes/functions из AST не подменяет этот показатель.

| Family | Конкретные реализации | N / excess | Предложение |
|---|---|---:|---|
| D1 — JSON write recorder + URI ref | `hh_application_audit.FakeStore/_Ref`, `hh_observe.FakeStore/Ref`, `hh_account_orchestration.FakeStore/Ref`, `hh_raw.FakeStore/_Ref`, `postgres_integration._AuditStore/_AuditRef` | **5 / 4** | Небольшой JSON audit recorder с явным capture metadata и изоляцией payload; thin adapter для реального digest допустим. Не моделировать внутри PG claims или S3 гарантию immutability |
| D2 — transaction-scope event recorder | `backfill_hh_postgres.FakeTransaction`, `materialize_hh_pending.FakeTransaction`, `postgres_storage.FakeTransaction` с их connections | **3 / 2** | Один scoped recorder begin/commit/rollback событий, только для orchestration. Его assertions не являются доказательством настоящего rollback |
| D3 — minimal full HH vacancy builder | `hh_filtering.vacancy`, `hh_cover_letters._vacancy`, `hh_application_audit._vacancy`, `postgres_integration._vacancy`, `postgres_storage._hh_vacancy` | **5 / 4** | Малый builder core fields с explicit overrides; expected facts остаются в case. Все пять consumers должны действительно использовать одну форму; иначе оставить локальные различия |
| **Всего по D1–D3** | **3 семейства, 13 реализаций** | **13 / 10** | До переноса сверить ownership, copy semantics и protocol shape; это candidate excess, не 10 автоматически удаляемых файлов |

Не включены в этот счёт:

- Read-oriented ETL `FakeStore` моделирует listing/metadata/relative_key и не равен JSON write recorder. Низкоуровневый `FakeS3Client` — ещё один protocol layer; blanket fake-store merger уничтожит читаемость.
- `MemoryClaimStore`, `MemoryQueryCursorStore`, `IdempotentTransactionalSink` и `SnapshotTransaction` повторяют production state algorithms. Их проблему решает правильный oracle, а не общий более большой fake.
- Account/reconciliation builders в трёх модулях похожи, но создают разные формы catalog/account/inventory. Shared reuse пока не доказан до уровня одного универсального builder.
- Пары `_config` и `_normalized_sql` занимают по две строки. Перенос в helper ради этих строк не обязателен.
- `EXPECTED_COLUMNS`, constraints/defaults/indexes — независимый schema oracle. Полное вычисление expected из production metadata создало бы тавтологию.

### 6.4. Большие inline HH payloads и модули

Основной размер ETL module — версии wire fixtures и fake persistence; основной размер schema module — expected schema; application/OBSERVE modules — setup и stateful doubles. Поэтому один общий «умный HH payload builder» не решает проблему.

Предлагается разделять **source fixtures** (`search page`, `search item`, `full vacancy`, `resume inventory`) и **локальные versioned envelopes** (`RAW-v2 application audit`, `RAW-v3 observation/evaluations`). Source fixtures обозначить synthetic/minimal либо зафиксировать реальную provenance при использовании capture; нынешние realistic dictionaries не выдавать за выгрузку HH.

Versioned golden payload для consumer compatibility должен оставаться независимым от producer serializer. Дополнительно полезен producer→consumer contract: именно такой тест обнаруживает R01. Нельзя использовать producer для автоматического построения всех expected payloads и считать schema compatibility проверенной.

Fixtures делать function-scoped с новой копией nested dictionaries; общий mutable dict может спрятать source-purity regression. Параметрам больших TOML/crossref matrices дать короткие смысловые IDs. Shared helpers размещать рядом с доказанными consumers, через явный импорт или узкий domain conftest. Root conftest оставить малым.

## 7. MISSING CURRENT REGRESSIONS

Это **30 логических regression families** для уже существующих production responsibilities и заявленных current invariants. Неполное доказательство не автоматически означает production bug. Где дефект подтверждён, указан способ; остальные строки — gaps или конкретный риск по коду. В CAR-140 tests/fixes не пишутся.

**P1** — безопасность отправки, потеря/искажение данных, recovery, destructive test target или существенный schema drift. **P2** — важная диагностика/поддерживаемость и более узкие edge cases. Это приоритет предлагаемых работ, не переименование существующих issue priorities.

### 7.1. Подтверждённые isolated probes

| Probe actual production path | Injected условия | Наблюдаемый результат | Предел доказательства |
|---|---|---|---|
| `run_apply_batch → HHApplicationAuditService` | Две допустимые vacancies; fake submit выбрасывает `RuntimeError("captcha_required")`; cap=2 | **2 submit calls, stopped_on_captcha=false, quota_consumed=2** | Подтверждён current error-routing defect; живой HH не вызывался |
| `run_apply_batch → audit service → claim port` | POST timeout; transition в UNCERTAIN выбрасывает persistence error; cap=1 | **2 submit calls, external_writes_attempted=0, quota_consumed=0** | Подтверждён quota-accounting defect на двух разных vacancy identities; PostgreSQL outage имитирован port exception |
| Actual uncertain outcome → `HHBatchOutcome.model_validate` | Outcome взят непосредственно из store предыдущего CAPTCHA run | **extra_forbidden на external_write_attempted** | Подтверждён producer/consumer incompatibility текущего RAW-v2 |
| `S3JsonStore.put_json` | Два разных bodies, один RAW-like key, injected S3 client | **2 put_object**, одинаковый key, разные bytes, отсутствуют conditional fields | Подтверждено отсутствие writer-side prevention; реальный overwrite/retention SeaweedFS не проверялся |
| `clean_postgres_dsn.__wrapped__` | `host=localhost hostaddr=203.0.113.10 dbname=careerops_integration_test`; _recreate_database заменён sentinel | Guard пропускает remote hostaddr до destructive setup; Alembic validator отвергает тот же ввод | Sentinel сработал **до connect**. Ни БД, ни schema не создавались/удалялись. Опасность — destructive test operation на непредусмотренном сервере, не утверждение о фактическом удалении production DB |

Для трёх production дефектов и небезопасного test fixture требуется regression вместе с отдельным исправлением в последующих задачах. Нельзя написать зелёный test, который закрепляет два POST при cap=1 или CAPTCHA continuation как «текущее корректное поведение».

### 7.2. Полный current regression backlog

В источниках ниже `hh/` означает `src/careerops_integrations/hh/`, `storage/` — `src/careerops_storage/`, `scheduler/` — `src/careerops_scheduler/`; `hh_s3_to_postgres.py` находится в `src/careerops_etl/`. Номера строк относятся к указанному в начале HEAD.

| ID / priority / evidence | Current invariant и production anchor | Что реально отсутствует | Regression shape / ожидаемый результат после исправления или усиления проверки |
|---|---|---|---|
| **R01 · P1**<br>Подтверждён изолированным прогоном | **APPLY producer → RAW-v2 outcome → ETL**<br>`apply_batch.py:382–400; hh_s3_to_postgres.py:517–538, 1152` | Фактически созданный uncertain outcome содержит external_write_attempted; HHBatchOutcome(extra=forbid) отвергает его с extra_forbidden. Existing failed fixture поля не содержит. | Component producer/consumer contract: normal/uncertain/failed outcomes всех текущих routes читаются; непроверенный failed outcome не превращается в application row. |
| **R02 · P1**<br>Подтверждён изолированным прогоном | **External attempts учитываются даже при отказе persistence**<br>`application_audit.py:195–213, 360–367; apply_batch.py:225–229, 382–427` | POST timeout + failure transition(UNCERTAIN) выходит как обычный RuntimeError; batch делает 2 попытки при cap=1, summary reports quota_consumed=0. Fake claim lifecycle tests этого не моделируют. | Component service→batch→account: каждая возможная внешняя попытка расходует allocation; failure после POST не открывает retry/следующую сверхлимитную отправку. Отдельно ошибки final claim/result persistence. |
| **R03 · P1**<br>Подтверждён изолированным прогоном | **CAPTCHA от HH до account isolation**<br>`application_audit.py:347–396; apply_batch.py:382–426; scheduler/dispatcher.py` | RuntimeError(captcha_required) из submit обёрнут в HHApplicationUncertain; batch обрабатывает его раньше CAPTCHA branch. Две попытки, stopped_on_captcha=false. Dispatcher test подставляет true вручную. | Component цепочка с fake transport: CAPTCHA на precheck/POST/confirmation прекращает account/bindings и передаёт pause; независимый account может работать. |
| **R04 · P1**<br>Подтверждён пробел writer guard; SeaweedFS не проверялся | **Immutable RAW при повторном ключе**<br>`storage/s3.py:275–302; hh/raw.py; application_audit.py` | Два put_json на один RAW key с разными bodies проходят до injected put_object без IfNoneMatch/IfMatch или чтения существующего объекта. UUID paths снижают коллизии, но не обеспечивают immutable API. | RAW-specific regression: повтор того же source допустим лишь по определённой idempotent semantics, другой body не заменяет существующий RAW; concurrent collision — отдельный real store proof. Не навязывать immutable put mutable scheduler mirrors. |
| **R05 · P1**<br>Подтверждён обход fixture guard без подключения | **Безопасный PostgreSQL test target**<br>`tests/test_postgres_integration.py:88–103; storage/alembic_cutover.py:180–250; alembic/env.py:20–40` | host=localhost + remote hostaddr достигает _recreate_database; вызов перехвачен sentinel до connect. Alembic validator тот же DSN отвергает. Fixtures используют несовместимую policy и разную область DROP. | Unit matrix непосредственно общего fixture entry: host/hostaddr, multi-host, db names, service/environment resolution, no runtime fallback, pinned URL precedence. До destructive connect доказать окончательный target; scoped reset/parallel ownership отдельно. |
| **R06 · P1**<br>Недостаточное integration доказательство | **Rollback actual RAW materialization**<br>`scripts/backfill_hh_postgres.py:45–54; hh_s3_to_postgres.py:1530–1604; tests ETL/PG helpers` | Fast rollback восстанавливает deepcopy fake; real PG rollback вызывает скопированный _materialize_observe_run. Ни один не вызывает actual transaction wrapper + ETL + real sink вместе. | Integration на Alembic head: инъекция ошибки после частичных v2/v3 writes actual loader; нет partial new rows/finished run, pre-existing rows неизменны, следующий run успешно коммитится. |
| **R07 · P1**<br>Недостаточное integration доказательство | **Idempotent actual v2/v3 replay**<br>`hh_s3_to_postgres.py:806–837, 1440–1604; storage/postgres.py` | Размер dict fake и replay скопированного pipeline не гарантируют production loader upsert semantics. | Integration: повтор одного versioned RAW run через production sink сохраняет canonical IDs, counts и одну pair application/work-item identity; incomplete→completed replay завершает run без дублей. |
| **R08 · P1**<br>Подтверждено чтением управляющего пути; DB-сценарий не запускался | **Materializer возобновляет ранее incomplete run**<br>`scripts/materialize_hh_pending.py:44–49, 93–98; scripts/backfill_hh_postgres.py` | SELECT id FROM observation_runs исключает любой существующий ID. Если backfill ранее сохранил incomplete run, появившийся RAW summary не делает его pending для daemon. Existing fixture моделирует только done ID. | Component + integration recovery: incomplete PG row и completed RAW остаются eligible; finished row пропускается; успешный повтор доводит состояние до finished. |
| **R09 · P2**<br>Отсутствуют веточные сценарии | **Selection и failure isolation CLI/materializer**<br>`scripts/materialize_hh_pending.py:52–76, 79–157; scripts/backfill_hh_postgres.py:68–77` | Нет materializer case с отсутствующим summary, bounded limit, load failure→следующий success; backfill не проверяет unknown run UUID и invalid limit. Ошибка header scan также не равна ошибке отдельного load. | Component с малым RAW catalog: отсутствие summary не читается как finished; ошибка имеет явный ненулевой report/exit; continuation внутри поддерживаемого per-run load. Политику corrupt header определить явно перед расширением recovery. |
| **R10 · P2**<br>Недостаточные adapter boundaries | **S3 resource lifecycle и malformed metadata**<br>`storage/s3.py:_S3KeySpace, S3JsonStore.__aenter__/__aexit__, _decode_object` | Hash mismatch и collected_at precedence уже покрыты. Не покрыты owned client close при exception, injected client ownership, пустые listing pages, malformed Metadata/LastModified и URI query/fragment. | Adapter component: resources закрываются владельцем; namespace rejected до I/O; повреждённый metadata не создаёт валидный provenance/ref. Не добавлять второй тест уже покрытого SHA mismatch. |
| **R11 · P1**<br>Недостаточные negative v3 contracts | **Полная vacancy×resume identity/fan-out и provenance**<br>`hh_s3_to_postgres.py:1235–1437` | Есть happy matrix и один submitted=1 mutation. Не проверены missing/duplicate/unknown resume, binding/version/target mismatch, projections, URI/account/profile mismatch и оценки для non-fetched vacancy. | Component parameter matrix по независимым corruption boundaries; actual transaction test гарантирует отсутствие успешной materialization при любой ошибке. Не требовать будущую queue семантику от текущего v3 envelope. |
| **R12 · P1**<br>Недостаточные negative v2 contracts | **Доказуемый application audit и cross-reference integrity**<br>`hh_s3_to_postgres.py:988–1136` | Один request resume mismatch не покрывает result/run/profile/mode/status/confirmed mismatch, reversed timestamps, absent audit identifiers и запрещённый чужой S3 namespace. | Component schema-v2 fixture mutations: отвергать непроверяемые связи, не изобретать application; разрешённый explicit vacancy_after failure остаётся accepted. |
| **R13 · P1**<br>Недостаточная entry-point matrix | **OBSERVE/no opt-in: zero employer-facing writes**<br>`hh/runtime.py:36–66; driver.py:112–125; batch_cli.py; application_cli.py; apply_batch.py:94–116` | Комбинации guard object и три transport/service barriers проверены. Не хватает from_env true/false/invalid/empty и настоящих CLI startup paths с запрещённым режимом/флагом до создания write-capable execution; public call_api non-GET route отдельно. | Fast boundary matrix с sentinel transport: ни один запрещённый entry point не вызывает employer-facing request, claim или APPLY workflow; valid APPLY требует также current published resume, selection и allocation на соответствующем account path. |
| **R14 · P1**<br>Недостаточные transport negatives | **Authoritative inventory и точное negotiation evidence**<br>`hh/driver.py:fetch_resumes, find_application_evidence; resume_sync.py` | Есть all-pages happy, missing items, incomplete negotiations metadata и чужой resume negative. Нет late-page transport/corrupt item inventory с доказательством no deletion и positive exact negotiation на поздней странице. | Adapter + reconciliation: failure/incomplete inventory не заменяет authoritative state; exact pair среди нескольких resumes/pages подтверждается, чужая pair — нет. |
| **R15 · P1**<br>Нет реального registry round trip | **PostgreSQL resume lifecycle/binding/history**<br>`storage/postgres.py:upsert_reconciled_resume, PostgresResumeRegistry; hh/resume_sync.py` | In-memory lifecycle tests сильны, PG test проверяет SQL params/row decoding. load фильтрует по account label плюс profile: нужна проверка rename без потери прежней deleted history; out-of-order observations также не доказаны. | Integration save→load→reconcile: active/deleted/published transitions, first_seen/history, no implicit binding inheritance, atomic failure. Rename и stale observations проверить на canonical source profile; потенциальный баг до DB-подтверждения так и маркировать. |
| **R16 · P1**<br>SQL shape вместо состояния | **Partial/full vacancy updates и время**<br>`storage/postgres.py:237–450` | Fake cursor не доказывает latest-observation wins, сохранение full fields после partial update и stable first_seen/last_seen/raw refs. | Integration: older/newer/full/partial в разных порядках; canonical IDs стабильны, partial не стирает full description/salary/operational fields, свежий RAW provenance согласован с current state. |
| **R17 · P1**<br>SQL WHERE не исполняется в negative test | **Claim ownership и safe retry transitions**<br>`storage/postgres.py:acquire_application_claim, transition_application_claim:1070; application_claims.py` | Concurrency и retry states уже проверены на PG, но stale owner/state transition получает заранее подставленный None. Нет доказательства неизменности чужого claim после stale UUID; identity normalization edge тоже не проверен. | Integration: старый application_run_id/неверный expected state не меняет row; FAILED_SAFE_TO_RETRY приобретает нового owner с attempt_count+1; ambiguous states не освобождаются. Blank/mismatched identity fail до внешней отправки. |
| **R18 · P2**<br>Недостаточное DB reservation покрытие | **Query cursor concurrency, wrap, catalog replacement**<br>`storage/postgres.py:1149–1250; hh/observe.py` | Два sequential real windows защищают psycopg modulo fix. Wrap проверен memory cursor; concurrent reservation, signature/size reset и source-profile isolation не доказаны real DB. | Integration с двумя connections и заданным overlap: корректные окна/offset bounds, определённый reset при изменении каталога, независимые profiles. Сохранять %% regression actual execution. |
| **R19 · P1**<br>Недостаточная quota validation matrix | **Scheduler/account не доверяют недостоверному summary**<br>`scheduler/dispatcher.py:500–557; hh/batch_cli.py:422–435` | Проверяются нормальные 2+1 расходы. Явный quota_consumed имеет приоритет над attempted/submitted; range check не доказывает согласованность counters, malformed types не покрыты. | Component child/account/dispatcher: missing/negative/over-limit/underreported counters, wrong account/mode и malformed summary fail conservatively; rejected result не возвращает уже возможные попытки в бюджет. Связанный confirmed failure — R02. |
| **R20 · P1**<br>Недостаточное recovery покрытие | **Scheduler restart/lock и persistent state**<br>`scheduler/dispatcher.py:_lock, _load_state, dispatch_once` | Tests читают сохранённую квоту, но не моделируют competing dispatchers, crash после worker start, stale running slot, write failure, restart с completed slot. | Filesystem/process component без HH: единый owner, completed slot не выполняется второй раз, uncertain worker не считается безопасным unused budget. Recovery policy должна быть явной; не выдумывать реализованную PG queue. |
| **R21 · P2**<br>Неполный planner contract; cache risk по коду | **Schedule windows, feasibility, deterministic plan reuse**<br>`scheduler/planner.py:generate_plan, ensure_plan:205–242` | Один seed/day проверяет gap. ensure_plan reuse проверяет schema/mode/accounts, но не все time-window/timezone/min-gap settings; изменение настройки может оставить старый план. | Unit/component: bounds/aware timezone/date rollover, impossible capacity даёт ошибку, same inputs/seed воспроизводимы, materially changed settings не возвращают нарушающий их cached plan. |
| **R22 · P2**<br>Недостаточное mapping покрытие | **Текущие HH field variants и canonical provenance**<br>`hh/mapper.py; src/careerops_contracts/vacancy.py` | Один realistic payload не покрывает salary_range vs salary, employment_form, work_format remote, missing employer/area, HTML normalization и RAW identity/hash/time mapping. | Unit небольшие versioned source variants с отдельными ожиданиями canonical/operational данных; никогда не трактовать vacancy-global already_interacted как pair authorization. |
| **R23 · P1**<br>Недостаточная post-submit confirmation матрица | **Успешный/неопределённый POST и audit completion**<br>`hh/application_audit.py:347–495; driver.py; hh_s3_to_postgres.py:_load_completed_application` | Есть POST timeout и precheck failure, но нет полного service proof для after-fetch failure, confirmation lookup failure/чужого resume и normal/test executor success с неоднозначным evidence. ETL crafted after-failure fixture не заменяет producer test. | Component: terminal claim не разрешает повторный POST, confirmed только для exact evidence; missing after описан явно, RAW читается ETL. Приоритет безопасности claim нельзя менять под будущую derived artifact-first схему. |
| **R24 · P1**<br>Исторический fix покрыт лишь конечной схемой | **0005 ремонт реально старой populated schema**<br>`sql/migrations/0005_repair_legacy_oltp_schema.sql; commit 77b902b` | SQL 0001 в текущем snapshot уже содержит audit columns и мягкую nullability. Применение 0001–0005 к пустой БД не воспроизводит старую installation, ради которой появился repair. | Legacy integration fixture первоначального состояния: отсутствующие audit columns, strict title/counters, existing rows → frozen 0005; данные сохранены, нужные nulls принимаются. Проверить повтор именно idempotent 0005, не всей non-idempotent chain. |
| **R25 · P1**<br>Catalog fingerprint не является data proof | **Populated legacy stamp сохраняет rows и IDs**<br>`alembic_cutover.py:714–751; tests/test_alembic_postgres_integration.py:77–103` | before/after stamp сравнивает DDL catalog. Нет seeded resumes, claims, applications, work items или проверки sequence после upgrade. | Integration: rows/canonical pair identities/claim terminal states и next generated IDs сохраняются после stamp→head; baseline CREATE не запускается на populated legacy. Fresh downgrade остаётся отдельным безопасным route. |
| **R26 · P1**<br>Runtime suite создаёт БД только legacy SQL | **Explicit psycopg SQL совместим с canonical Alembic head**<br>`tests/test_postgres_integration.py:76–84; tests/test_postgres_storage.py:686–802; storage/postgres.py` | Regex legacy-column parser и real runtime suite используют SQL 0001–0005; Alembic tests отдельно проверяют DDL. После нового revision эти две проверки могут расходиться. | Integration runtime writes/reads через PostgresOLTPStore/claims/registry на graph head; legacy SQL нужен только historical path. Не добавлять искусственные будущие columns ради зелёных tests сегодня. |
| **R27 · P1**<br>Только один positive live drift signal | **Semantic schema drift, особенно PK/check/unique**<br>`storage/alembic_cutover.py:415–468, 494–642; tests/test_storage_schema.py` | Positive detector test меняет лишь extra column. Custom PrimaryKeyDrift не имеет negative mutation; checks в focused validator проверяются по именам, не по полноценной семантике. | Integration controlled drift для pair PK/unique/FK, critical check expression, type/default/nullability/index predicate. Detector обязан сигналить существенное расхождение; declarative expected oracle не строить из самого проверяемого metadata. |
| **R28 · P2**<br>Нет file-history integrity guard | **Frozen SQL 0001–0005 и self-contained baseline**<br>`sql/migrations/*; alembic/versions/20260904_0005_current_schema_baseline.py; test_alembic_migrations.py` | Graph, final schema и op recorder не замечают согласованную правку исторических SQL/metadata. Пользователь прямо закрепил immutable migration history. | Governance/contract: approved baseline file hashes или эквивалентная проверка diff относительно принятой истории; revision не импортирует изменяемое live metadata. Ожидания утверждаются по history, не генерируются заново при каждом запуске. |
| **R29 · P1**<br>Проверены happy writes, не durable failures | **Текущий RAW/audit отказ не выдаёт ложный success**<br>`hh/observe.py; application_audit.py; hh/raw.py; storage/s3.py` | Purity и earlier-page recovery не доказывают последствия failed put page/vacancy/sidecar/result. Fake JSON store всегда успешно принимает записи. | Component injected storage failure: отсутствует ложный complete/finished отчёт об успешно сохранённом RAW; уже durable source остаётся доступным. До POST отказ request audit блокирует отправку; после POST действует uncertainty/attempt accounting R02/R23. |
| **R30 · P2**<br>Нет повторяемости текущего letter generator | **Deterministic factual cover-letter output**<br>`hh/cover_letters.py` | Existing tests проверяют company/title, skills и domain, но не same-input repeat, duplicate skills/order handling и fallback без вымышленных facts. | Unit повторяемость текущей функции и factual fallbacks. Не подменяет FUTURE deterministic final matching score и не требует LLM evaluation suite. |

### 7.3. История fixes и сохранение имеющейся защиты

| Проверенный local history | Что уже защищено | Что не следует считать покрытым |
|---|---|---|
| `77b902b` — Fix PostgreSQL production upgrade path | Реальный psycopg cursor reservation test защищает исправление modulo placeholder; canonical schema tests фиксируют финальную nullability/audit columns | Исходная populated legacy schema перед 0005 не воспроизведена: R24 |
| `251d716` — CAR-46 remove obsolete v2 restriction from Alembic validator | `test_live_schema_accepts_future_v2_table_declared_by_metadata` и dynamic-head orchestration — KEEP | Offline baseline test всё ещё запрещает V2 tables во всём head; rewrite нужен уже сейчас |
| `b7a9361` — CAR-46 migration/schema drift integration | Fresh/legacy/round-trip и extra-column positive detector — KEEP | Populated data preservation, custom PK/check-expression drift и actual runtime SQL on head: R25–R27 |
| `914b854` — Automate HH RAW materialization | Один pending/done/APPLY selection component scenario | Recovery уже имеющегося incomplete row, missing summary и failure continuation: R08–R09 |

Current test count не должен скрывать эти holes: 208 passed совместимы с подтверждёнными ошибками на межкомпонентных границах.

## 8. FUTURE_CONTRACTS — только после реализации Feature

Эти **12 contract families** выведены из утверждённого пользователем ближайшего roadmap. Они не входят в 219, не являются требованием сегодняшнего green suite и не предполагают создание сейчас tests/fixtures для несуществующих processors/queues/ClickHouse.

| ID | Будущий контракт | Trigger реализации | Проверяемый invariant | Связь с текущей защитой и границы |
|---|---|---|---|---|
| **F01** | Lossless discovered backlog при technical limits | DISCOVERY/backlog Feature | Каждый discovered vacancy из durable RAW pages имеет persistent backlog identity/status независимо от max_unique/max_full_fetch/rate limit, page retry или crash. Conservation: discovered set = persisted/explicitly pending set; никакого silent drop. | Сейчас discovery RAW сохраняется шире candidate sidecars; test_observe_enforces_* не является целевым контрактом. |
| **F02** | Persistent full-fetch queue | Full-fetch queue Feature | Reservation/lease/retry переживают restart и account throttle; transient 429/timeout оставляет work pending; обработка воспроизводима и не теряет source identity. | Current query cursor и bounded loop не доказывают queue durability. |
| **F03** | Primary high-recall filter | Primary filter Feature | Broad discovery не режется legacy ML title whitelist; explicit reject vs deferred техническая обработка различаются. Проверять согласованный high-recall corpus/явные policy причины, не магический процент coverage или старые 29 regex cases. | User exclusions сохраняются на одобренной policy стадии без уничтожения source/backlog. |
| **F04** | Immutable RAW и отдельные derived artifacts | Derived storage/extraction Feature | Extraction/intelligence имеет отдельный namespace/format/version/lineage к RAW hash; повтор processing не модифицирует RAW и не добавляет локальные поля в source body. | RAW immutability уже current R04; здесь дополнительный контракт нового derived writer. |
| **F05** | Derived artifact-first semantics | Derived processing/materialization Feature | Durable derived artifact подтверждён до успешного PG materialization status. Crash до write, после write/до PG commit и после commit даёт replayable recovery без false success и без утраты artifact. | Не переносить эту схему механически на external application claim: там SUBMITTING/terminal state предотвращают повтор POST. |
| **F06** | Replayable versioned processing | Processing/versioning Feature | RAW hash + processing/extraction/model/scoring/binding versions определяют воспроизводимую lineage; reprocess новой версии не подменяет старый artifact и не создаёт duplicate online application. | Archived v2/v3 decoding остаётся compatibility obligation, даже если новые producer envelopes меняются. |
| **F07** | Vacancy × resume evaluation и requirement-to-evidence | Extraction/evaluation/reranking Feature | Разные resumes имеют отдельные evaluations и evidence alignment; requirement unsupported/contradicted evidence не превращается в факт кандидата. Provenance query overlap — routing metadata, не финальный match score. | Current fan-out/claims KEEP дают основу identity; не тестируют несуществующий reranker. |
| **F08** | Deterministic final scoring | Scoring Feature | При одинаковых persisted intelligence/evidence inputs и version/config выходные score, ordering/tie-break и decision совпадают; явно заданы numeric normalization/threshold boundaries. | Determinism scoring не означает требование детерминированного внешнего LLM при каждом повторном extraction; тестировать фиксированные artifacts. |
| **F09** | Persistent application candidates не теряются при throttle | Application candidate queue Feature | Quota/rate limit/CAPTCHA/per-account pause оставляют candidate pending/deferred с причиной и доступным retry; технический throttle не превращается в permanent rejection или delete. | Сегодня APPLY loop возвращает summary, persistent candidate queue ещё отсутствует. |
| **F10** | Bounded APPLY поверх queues и existing pair claims | Queue-to-APPLY Feature | Revalidate mode+opt-in, current published/bound/selectable resume, account allocation и exact evidence; canonical pair (resume_id,vacancy_id) получает не более одной отправки, ambiguous attempt консервативно расходует budget. | Существующие guards/atomic claims должны сохраниться при замене orchestration; нельзя списать их как legacy matching. |
| **F11** | PG operational/control plane, ClickHouse вне online decision | Persistent orchestration Feature; отдельно будущая ClickHouse integration | Queue state/claim/decision ownership восстанавливаются из PostgreSQL. Online eligibility/score/application не зависит от lag/доступности ClickHouse; он хранит history/metrics/aggregates по более позднему roadmap. | До реализации ClickHouse нет причин добавлять runtime dependency или mock ClickHouse test сейчас. Текущий scheduler JSON exception зафиксирован явно. |
| **F12** | V2 schema evolution через Alembic и legacy compatibility | Каждая реальная V2 schema Feature | Новые реальные revisions проверяются fresh head и populated legacy stamp→descendants; SQL 0001–0005 неизменны. Привязанные к реализованной schema runtime writes и upgrade compatibility подтверждаются вместе. | Текущие graph/head-flexibility и frozen-history tests нужны сегодня; выдуманные V2 таблицы ради теста — нет. |

**Retirement gate:** конкретная legacy responsibility заменена в production, downstream compatibility сохранена, relevant FUTURE_CONTRACT реализован и проверен, ingestion/replay работают. Только после этого удалять соответствующие RETIRE_AFTER_V2 cases. Само появление V2 Feature/таблицы или включение нового модуля не является основанием выключить все 30 tests.

**Current vs future:** immutable RAW, guards, pair claims и Alembic history уже текущие обязательства (R04/R13/R17/R28). Lossless backlog, durable full-fetch/candidate queues, derived artifact-first, requirement-to-evidence и final scoring ещё не реализованы. Их missing implementation не следует оформлять как сломанный сегодняшний unit test.

## 9. Proposed target test architecture

Разделять по **реальной проверяемой границе**, а внутри — по domain. Component использует настоящий workflow/adapter и подменяет external ports; integration использует реальный внешний storage engine. «Fast» остаётся execution selection, не синонимом unit.

Ниже только схема размещения существующих/реализованных responsibilities, не созданные каталоги:

```text
tests/
  conftest.py                     # только общие platform/temp mechanics
  unit/
    hh/
      runtime_guards/
      apply_eligibility/
      resume_reconciliation/
      mapping/
      cover_letters/
      legacy_apply_filtering/     # current gate, retirement по Feature
      configuration/
    scheduler/
      planner/
      quota_policy/
    storage/
      schema_contracts/
      row_decoding/
    migrations/
      graph/
      frozen_history/
    packaging/
  component/
    hh/
      driver_contracts/
      observe_discovery/
      observe_provenance/
      observe_evaluations/
      application_guards/
      application_audit/
      account_apply/
    etl/
      raw_v2_contracts/
      raw_v3_contracts/
      backfill/
      pending_materializer/
    storage/
      s3_adapter/
      local_raw/
    scheduler/
      dispatch_state/
      worker_contracts/
    migrations/
      offline_cli/
      cutover_orchestration/
  integration/
    postgres/
      conftest.py                 # scoped disposable targets; один safety owner
      runtime_upserts/
      claims/
      resume_registry/
      query_cursor/
      raw_materialization/
      alembic_fresh/
      alembic_legacy/
      schema_drift/
    s3/                          # опционально: actual current RAW semantics
  support/
    hh/                          # только доказанный D1/D3 reuse
    etl/                         # versioned wire data / scope recorder при reuse
```

Это domain map, а не требование создать отдельную директорию для каждого одного test. При малом размере соответствующие листы — обычные файлы. No-op перемещения без улучшения владельца ответственности не нужны. FUTURE_CONTRACTS остаются в документации/соответствующих Feature criteria; каталога с заранее написанными failing V2 tests нет.

Практические правила:

1. **Каждому SQL invariant нужен подходящий oracle.** Domain guard до I/O остаётся fast. Row decoding можно проверить tuple input. UPSERT/transaction/locking/PK/FK/unique/temporal merge проверяются actual PostgreSQL и row state. Замена SQL implementation при сохранении поведения не должна массово ломать tests.
2. **Runtime DB suite строится через current Alembic head.** Frozen legacy chain — отдельный migration entry fixture. Сначала общий DSN validator и ownership/reset policy; fixed global database и `xdist` нельзя совмещать без уникальных targets.
3. **Нужны короткие межкомпонентные contracts.** Actual producer создаёт RAW, current ETL принимает его; service ошибки проходят до batch/account/scheduler. External HH port остаётся fake/sentinel, реальная отправка для regression suite не требуется.
4. **Expected data независимы от production algorithm.** Golden archived v2/v3 examples проверяют совместимость истории; actual producer tests дополняют их. Stateful fake не должен быть второй реализацией database, correctness которой suite будто бы доказывает.
5. **Local fixture first.** Shared support только D1–D3 после проверки consumers. Async events/clock/temporary files — scope по потребности. Нет giant root conftest, autouse transport patch на весь suite или универсального fake-контейнера всего приложения.
6. **Сохранить visibility vendor boundary.** CareerOPS adapter contracts — здесь; vendored HH UI/AI/transport suite — отдельно с собственными dependencies и результатом.
7. **Markers/CI — отдельная последняя настройка.** Существующий `integration_postgres` можно сохранить как execution boundary при переносах. Новый optional S3 marker вводится только вместе с безопасным реальным provider fixture. В CAR-140 ничего не меняется.

## 10. Порядок выполнения CAR-141…CAR-146

Официальный scope задач CAR-139:

- CAR-141 — Define fast and integration test suite boundaries
- CAR-142 — Consolidate duplicated test fixtures and builders
- CAR-143 — Replace oversized inline payloads with minimal fixtures
- CAR-144 — Remove redundant and implementation-detail tests
- CAR-145 — Split oversized HH test modules by behavior
- CAR-146 — Establish measured coverage duration and flaky-test policy

### CAR-141 — Define fast and integration test suite boundaries

Цель:

Формально определить execution boundaries test suite без создания лишней marker taxonomy.

Предлагаемая модель:

FAST:
- pure unit tests;
- contract tests без external services;
- component tests с fakes;
- local filesystem;
- Alembic offline/programmatic checks.

INTEGRATION_POSTGRES:
- настоящий disposable PostgreSQL;
- реальные constraints / transactions / locking;
- runtime persistence;
- migration fresh/legacy paths;
- schema drift.

В будущем, только после появления соответствующей CI Feature:

INTEGRATION_S3:
- настоящий disposable SeaweedFS/S3;
- RAW immutability;
- metadata/integrity;
- writer/readback semantics.

Exit criteria:

- каждый текущий test module имеет однозначный execution layer;
- integration_postgres не содержит fake-DB tests;
- fast suite не требует external services;
- не вводятся markers только ради domain classification;
- PostgreSQL destructive target policy определена отдельно от обычных fixtures.

Примечание:

Найденный CAR-140 DSN hostaddr safety defect должен быть исправлен отдельной Bug/Task до полноценного PostgreSQL integration CI и не должен скрываться внутри test cleanup.

---

### CAR-142 — Consolidate duplicated test fixtures and builders

Цель:

Убрать только доказанное дублирование test infrastructure.

Основные кандидаты из CAR-140:

- PostgreSQL fake cursor/connection families;
- HH vacancy builders;
- resume builders;
- S3 fake stores;
- ETL scope/transaction recorders;
- application audit helpers.

Правило:

Похожий код не является достаточной причиной для shared fixture.

Shared helper создаётся только если:

- semantics одинаковы;
- минимум несколько consumers;
- abstraction уменьшает duplication;
- abstraction не скрывает важные параметры test case.

Не создавать giant root conftest.py.

Domain-specific support размещать рядом с соответствующим domain.

Exit criteria:

- подтверждённые duplicate implementations сокращены;
- state между tests не протекает;
- test readability не ухудшилась;
- specialized fakes остаются локальными, если semantics различаются.

---

### CAR-143 — Replace oversized inline payloads with minimal fixtures

Цель:

Уменьшить шум от больших HH/S3 payloads, не уничтожив wire-compatibility coverage.

Основные кандидаты:

- test_hh_s3_to_postgres.py;
- test_hh_observe.py;
- test_hh_application_audit.py;
- test_hh_configuration.py;
- test_hh_runtime.py.

Правило:

Минимальный fixture должен содержать только поля, влияющие на конкретный invariant.

Отдельно сохраняются versioned/golden payloads, когда предмет теста:

- RAW v2/v3 compatibility;
- source schema compatibility;
- historical replay.

Не использовать один giant "realistic vacancy" builder с десятками default values.

Exit criteria:

- oversized repeated payloads сокращены;
- negative absent/malformed cases остаются явными;
- historical RAW examples не потеряны;
- expected values не генерируются production algorithm.

---

### CAR-144 — Remove redundant and implementation-detail tests

Цель:

Применить результаты CAR-140 категорий:

- DELETE_NOW;
- MERGE;
- REWRITE implementation-detail oracles.

Не затрагивать RETIRE_AFTER_V2 без соответствующего production cutover.

Особое внимание:

- SQL string shape;
- parameter positions;
- duplicated schema assertions;
- internal call ordering;
- duplicated behavioral scenarios.

Правило удаления:

Test удаляется только если:

1. invariant больше не существует; или
2. другой surviving test доказывает тот же failure mode не слабее.

Для SQL:

Named schema/constraint semantics сохраняются.

Хрупкое форматирование SQL и внутренние детали implementation могут заменяться behavioral PostgreSQL tests.

Exit criteria:

- у каждого DELETE/MERGE есть mapping old → surviving oracle;
- meaningful regression protection не уменьшилась;
- RETIRE_AFTER_V2 остаётся active до V2 retirement gate;
- необходимые behavioral DB replacements появляются до удаления слабых fake/SQL-shape tests.

---

### CAR-145 — Split oversized HH test modules by behavior

Цель:

Разделить только действительно mixed-responsibility HH modules.

Основные кандидаты CAR-140:

- test_hh_s3_to_postgres.py;
- test_hh_observe.py;
- test_hh_application_audit.py;
- другие >500 lines только после semantic review.

Split выполняется по behavior/domain responsibility, а не по line count.

Например:

observe:
- discovery;
- provenance;
- evaluation fan-out;
- failure behavior.

application:
- guards;
- claims;
- upstream evidence;
- uncertainty;
- audit artifacts.

ETL:
- wire decoding;
- materialization;
- replay/idempotence;
- failure/transaction behavior.

Exit criteria:

- один module имеет coherent responsibility;
- helpers не превращаются в новую свалку;
- test names и failure output показывают конкретный subsystem;
- все прежние meaningful assertions сохранены.

---

### CAR-146 — Establish measured coverage duration and flaky-test policy

Цель:

После cleanup получить измеренный новый baseline и правила сопровождения test suite.

Зафиксировать:

- total collected;
- fast count;
- PostgreSQL integration count;
- optional S3 integration count, если он уже реализован;
- fast duration;
- PostgreSQL integration duration;
- slowest tests;
- flaky/retry policy;
- skipped/deselected policy.

Coverage:

Не вводить arbitrary coverage percentage target.

Использовать coverage как диагностический signal для обнаружения непроверенных critical paths, а не KPI.

Flaky policy:

- flaky test не разрешается бесконечно rerun-ить;
- известная flakiness должна иметь owner/root cause;
- retry может использоваться только как временная диагностическая мера;
- deterministic local fast tests не должны быть flaky.

Exit criteria:

- baseline повторяем и документирован;
- CI selection соответствует CAR-141;
- required integration suite не может silently skip;
- known flaky tests либо исправлены, либо имеют явный tracked issue;
- будущие FUTURE_CONTRACT tests добавляются только вместе со своими Features.
## 11. Risk analysis

| Cleanup-действие | Как можно потерять regression protection | Условие безопасного выполнения |
|---|---|---|
| Слить все OBSERVE/APPLY guard tests | Оставить защищённым service, но открыть direct driver/test bridge/API path | Сохранить tests каждой достижимой write boundary и отрицательные mode/opt-in комбинации |
| Удалить «похожий» resume-specific case | Вернуть global relations dedup, заблокировать второе resume или разрешить duplicate после rename | Отдельно exact same pair, second resume, account label rename, DB canonical uniqueness |
| Заменить реальную claim concurrency memory fake | Проверять asyncio.Lock fake вместо PostgreSQL conflict/ownership | Actual two-connection winner и retry/non-retryable/stale-owner state evidence |
| Сохранить только dictionary replay/rollback | Ошибка transaction wrapper/UPSERT станет невидимой | Production loader + real sink + real transaction; fault injection после фактических частичных writes |
| Удалить `TransactionStatus.IDLE` assertions при выносе driver | HH/S3 wait окажется внутри PG transaction и будет держать locks | Сохранить network-outside-transaction invariant в actual service+PG scenario |
| Считать любой SQL assertion implementation detail | Потерять canonical pair FK/unique, nullable repair, defaults или partial index semantics | Различать формат SQL и named declarative semantics; существенные constraints подтвердить actual rows/drift |
| Генерировать expected schema из production metadata | Согласованная ошибка будет «доказана» одинаковыми expected/actual | Independent domain expectations + live migration diff + controlled positive mutation |
| Переписать legacy SQL ради нового test | Повредить installation/cutover history и replay | Frozen 0001–0005; новые schema changes только реальными Alembic descendants |
| Удалить old RAW-v2/v3 fixtures как legacy | Потерять backfill уже сохранённых audit objects | Historical wire compatibility сохраняется независимо от retirement текущего producer |
| Сразу удалить 30 RETIRE tests | Сломать действующий APPLY до включения V2 либо потерять технические bounds | Retirement отдельно для заменённой responsibility; required current ingestion smoke до и после cutover |
| Оставить current bounded-loop expectation в V2 | Закрепить исчезновение deferred vacancy/application из pending processing | F01/F02/F09 проверяют conservation/durable state; technical limit и business reject различаются |
| Большой shared HH builder с auto-defaults | Defaults сами исправят испорченный payload, неверный expected или absent field | Minimal source fixtures, explicit overrides, negative absent/malformed cases и independent expectations |
| Общий mutable FakeStore/fixture | Cross-test mutation скрывает RAW purity или создаёт order dependence | Fresh data per case, явная copy semantics, no global mutable registry |
| Ускорить suite mock-заменой offline Alembic/DB/FS | Удалить boundary, ради которой test нужен | Сначала сохранять доказательство; ~2.4 s fast не требует такого tradeoff |
| Объединить unsafe fixed-name PG fixtures/включить parallelism | DROP на чужом host/соседнем worker, flaky/опасный cleanup | R05 до расширения integration; final resolved target, unique ownership, scoped cleanup |
| Включить vendor tests в общий denominator | Смешать product и upstream failures, тяжёлые UI/dependencies и ложную coverage цель | Отдельный suite/result и небольшой CareerOPS adapter contract |
| Применить derived artifact-first к external POST state machine | Отложить terminal claim до S3 write и разрешить повтор внешней попытки | Разделять derived success materialization и application duplicate-safety protocol |
| Провести cleanup одним большим commit | Невозможно сопоставить удалённые inputs с survivors; ложный зелёный baseline | Небольшие issue-scoped изменения с mapping до/после и подходящими проверками |

## 12. Proposed before/after metrics

Вклад CAR-140 в suite count — **нулевой**: исходный snapshot остаётся базой 219 total / 208 fast / 11 integration. Параллельные незавершённые изменения другой задачи не включены в эти показатели. Следующие значения — способ измерения будущего cleanup и условная арифметика, не выполненный результат и не quota на удаление tests.

| Метрика | Before (проверено) | После семантической реорганизации: предложение |
|---|---:|---|
| Total collected | **219** | Нет целевого «меньше N». Если приняты все 6 DELETE/MERGE, без иных изменений база **213**; затем прибавляются meaningful SPLIT и current regression cases |
| Fast collected | **208** | Условная база **204** после четырёх fast consolidation donors; перенос DB responsibilities меняет layer, новые fast regressions/splits могут увеличить count |
| Integration PostgreSQL | **11** | Условная база **9** после двух integration donors; добавить actual ETL/runtime-head/negative-state coverage. Не ставить цель уменьшить число DB cases |
| Fast-suite pytest duration | Исторически ~2.35 s; audit **2.44 s** | Сохранять порядок текущего времени на том же host/venv; ориентир около 2–3 s для сопоставимого scope, **не SLA**. Сравнивать одинаковую selection до/после; новые полезные cases обосновывают прирост |
| Integration runtime | Не измерялся | Сначала безопасный actual baseline fresh/legacy/runtime; не придумывать ожидаемые секунды |
| Duplicated infrastructure | D1–D3: **13 implementations / 10 candidate excess** | При доказанном reuse ожидаемо **3–6 implementations / 0–3 excess** в этих же families. Protocol-specific adapters могут сохранить часть копий; счёт не включает fixture oracles |
| Oversized test modules >500 lines | **6** | Предложение **0–2** после разделения responsibilities; independent versioned/schema data может обоснованно превышать порог. Не переносить все строки в один giant fixture module |
| Long test functions >80 lines | **8** | Контролировать ответственность/диагностику, а не механический line-count gate; большой migration scenario допустим, если cohesive |
| Current missing regression families | **30** | Закрывать по evidence; P1 first. Одна family может потребовать несколько parametrized cases или усиления существующего |
| Future contracts | **12, только документ** | Добавляются в tests по соответствующему Feature trigger; до реализации test count для них **0** |
| Meaningful behavioral loss | Не применимо | **0** удалённых invariants без эквивалентного surviving oracle |

Для прозрачного пересчёта после первых шести consolidations:

- `total = 213 + S + R`;
- `fast = 204 - M + S_fast + R_fast`;
- `postgres = 9 + M + S_pg + R_pg`.

Здесь **S** — чистый прирост collected cases от SPLIT, **R** — чистый прирост от current regressions, **M** — число сохранившихся cases, реально перемещённых fast→PG без изменения количества. Если несколько tests заменены одним более сильным scenario или появились optional S3 integrations, это отдельное явно показанное изменение формулы. Parameterization с теми же 17 lexical inputs не считается сокращением 17 behavioral cases.

Не устанавливать coverage-percentage target. Основные review metrics — какие production failure modes пойманы, сколько ложных SQL/fake oracles заменено, сколько critical current gaps закрыто, сохранена ли история/replay и понятен ли ownership modules.

## 13. Проверка полноты и verdict

- Inventory включает **24/24** modules, **196/196** functions и **219/219** collected cases; каждая функция/parameter group встречается в module decision table, ни один case не классифицирован дважды.
- Таблица primary verdicts сходится: **141 + 30 + 5 + 1 + 30 + 5 + 7 = 219**. Case-level MOVE/SPLIT не прибавлены повторно как module actions.
- Список current gaps содержит **R01–R30**; будущие obligations — **F01–F12**. Existing hash mismatch, current graph flexibility, global-relations safety и actual psycopg cursor fix признаны покрытыми, не объявлены missing повторно.
- Fast baseline выполнен и прошёл; PostgreSQL integration прочитан/collected, но не запускался. Source-changing actions и реальные внешние отправки отсутствовали.
- Единственный результат CAR-140 — `docs/testing/TEST_SUITE_AUDIT.md`; все реорганизации/исправления в документе остаются предложениями. Появившиеся во время аудита посторонние changes/branch switch сохранены; финальный working tree не объявляется чистым или повторно протестированным.

**Verdict:** явно KEEP — **141**; кандидатов DELETE/MERGE — **6** (1 + 5); REWRITE — **30**; RETIRE_AFTER_V2 — **30**; case-level SPLIT/MOVE — **12** (5 + 7); missing current regression families — **30**; future contract families — **12**.

Пять изменений архитектуры tests с максимальным эффектом:

1. **Общий безопасный PostgreSQL integration entry и runtime schema через Alembic head**, с отдельным populated legacy path.
2. **Actual ETL/SQL behavioral oracles** вместо dictionary UPSERT, snapshot rollback и SQL-shape доказательств.
3. **Короткие producer→consumer и error-propagation component contracts** для RAW-v2 compatibility, quota failures и CAPTCHA/account isolation.
4. **Разделение шести больших модулей по domain responsibility и минимальный доказанный reuse D1–D3**, с сохранением независимых versioned fixtures.
5. **Явное разделение current safety/history, temporary legacy gate и Feature-trigger V2 contracts**, без преждевременного retirement действующего ingestion.
