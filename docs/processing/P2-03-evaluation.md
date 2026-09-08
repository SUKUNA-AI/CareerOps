# P2-03 — оценка качества и первый baseline

## Что зафиксировано

P2-03 использует отдельный evaluation layer, который принимает точные `NormalizedVacancy`, опциональный `NormalizedResume` и неизменяемый `TargetPolicy`

Главная метрика фильтра — false exclusion rate, потому что P2-03 имеет право только доказанно исключать вакансию и не должен терять потенциально подходящие варианты

## Стартовый корпус

Первый корпус `filter-bootstrap-v1` содержит 26 пограничных случаев

- 17 случаев должны завершаться `KEEP`
- 9 случаев должны завершаться `EXCLUDE_PROVEN`
- покрыты неоднозначные роли, incidental technologies, C++, Java Backend, remote/location, отрицание relocation, неизвестный work format, forbidden context, availability и experience gap

Baseline текущего P2-03:

- false exclusions: `0 / 17`
- retention false-negative rate: `0.0`
- missed proven exclusions: `0 / 9`
- reason mismatches: `0 / 9`

У этого корпуса `release_gate_eligible=false`

Он нужен для стартовых regression checks и не заменяет реальную размеченную выборку HH

Production release gate по FNR можно включать только после появления `REAL_ANNOTATED` corpus из фактического discovery потока

## Запуск

```bash
python -m careerops_processing.evaluation \
  --policy-dir config/processing/target_policies \
  --output docs/processing/baselines/filter-bootstrap-v1.json
```

Команда evaluation возвращает ненулевой код, если обнаружен false exclusion, missed exclusion или reason mismatch
