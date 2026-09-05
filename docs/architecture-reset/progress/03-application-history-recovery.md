# CareerOPS Architecture Reset — восстановление истории откликов

Дата анализа: 2026-09-06

Источник: архив S3 application audit, выгруженный из `careerops-raw`. Архив содержал только `application_request.json` и `application_result.json` для исторических application attempts. Полные payload сообщений, компаний и upstream response в repository не сохраняются: этот документ фиксирует только агрегированную recovery-информацию.

SHA-256 локально проанализированного архива:

```text
a538b82e2793619e10595a6270baef7ad74797d48fbc6169a64957e851d4a1b2
```

## Сводка

В архиве найдено:

- 189 `application_request.json`;
- 189 `application_result.json`;
- 189 application attempts;
- 176 уникальных vacancy identities;
- один исторический profile/resume identity во всех attempts.

Распределение attempts по результату:

| Result | Attempts |
| --- | ---: |
| `submitted`, `confirmed=true` | 168 |
| `unconfirmed`, `confirmed=false` | 4 |
| `failed` | 17 |

Распределение по transport mode:

| Mode | Attempts |
| --- | ---: |
| `negotiations_api` | 155 |
| `upstream_hh_test` | 34 |

Все 17 failed attempts имеют одну и ту же семантику ошибки: HH вернул `Daily negotiations limit is exceeded`. Это не ambiguous transport outcome и не доказанный successful submission.

## Identity-level recovery

После схлопывания повторных attempts одной и той же пары resume × vacancy получено 176 уникальных application identities:

| Recovery class | Unique vacancy identities | Semantics for CareerOPS v2 |
| --- | ---: | --- |
| `CONFIRMED_SUBMITTED` | 168 | Запретить автоматический повторный submit |
| `SUBMITTED_UNCONFIRMED` | 4 | Fail closed: не повторять автоматически до явной reconciliation с HH |
| `FAILED_ONLY` | 4 | Историческая попытка не доказала submit; будущий retry допустим только через новый guarded application owner и fresh upstream precheck |

Из 17 failed attempts часть была повторена позже после снятия лимита. Восемь уникальных vacancy identities после одного или нескольких `limit_exceeded` attempts в итоге получили подтверждённый `submitted`. Поэтому raw attempt count нельзя использовать как application identity count.

Распределение количества attempts на одну vacancy identity:

- 167 identities: 1 attempt;
- 5 identities: 2 attempts;
- 4 identities: 3 attempts.

## По датам

| Date | Confirmed submitted | Unconfirmed | Failed attempts |
| --- | ---: | ---: | ---: |
| 2026-08-30 | 80 | 0 | 0 |
| 2026-08-31 | 66 | 4 | 17 |
| 2026-09-01 | 21 | 0 | 0 |
| 2026-09-02 | 1 | 0 | 0 |

Итого: 168 confirmed submitted, 4 submitted-unconfirmed, 17 failed attempts.

## Важное расхождение со старым PostgreSQL

Live PostgreSQL inventory показал `applications = 0` и `application_claims = 0`, однако S3 application audit содержит реальную историю внешних HH submit attempts.

Следовательно:

- PostgreSQL application tables текущего deploy нельзя считать полным source-of-truth исторических откликов;
- S3 application audit является необходимым recovery source для старых application facts;
- DB reset без импорта application history создаст риск повторного автоматического отклика на уже обработанные vacancy identities.

## Правило миграции в CareerOPS v2

Перед включением нового guarded APPLY необходимо выполнить one-time import исторических application identities.

Минимальный безопасный operational state после импорта:

- 168 identities -> terminal historical state, эквивалентный `SUBMITTED/CONFIRMED`;
- 4 identities -> terminal/blocked historical state `SUBMITTED_UNCONFIRMED` или `UNCERTAIN`, запрещающий автоматический retry до reconciliation;
- 4 identities -> не создавать ложный submitted fact; сохранить failure history в Lake/artifacts, а перед возможным будущим submit выполнить fresh duplicate/pre-existing negotiation check.

Исторические retries и все 189 attempts должны оставаться в S3/Lake как append-only audit/history. PostgreSQL v2 должен хранить компактный authoritative current application state, а не копировать каждый старый run как отдельную operational row.

## Safety invariant

Новый application owner обязан работать fail-closed:

```text
confirmed historical submit
    -> NEVER AUTO-RETRY

submitted but unconfirmed historical result
    -> BLOCK AUTO-RETRY UNTIL RECONCILED

explicit safe failure without external submit evidence
    -> MAY RETRY ONLY AFTER FRESH UPSTREAM PRECHECK
```

Application identity key окончательно фиксируется после отдельного исследования HH same-account / multi-resume semantics. До этого нельзя ослаблять duplicate protection.

## Reset verdict для application history

```text
APPLICATION_HISTORY_RECOVERABLE
```

История достаточна, чтобы не потерять факт старых submit attempts при PostgreSQL reset, при условии one-time import перечисленной выше identity-level семантики перед включением нового APPLY.

Сам application audit archive не должен коммититься в публичный repository: он содержит сообщения откликов, названия компаний и upstream payload.
