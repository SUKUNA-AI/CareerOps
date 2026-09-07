# P2-03 — Gold evaluation и первый baseline

## Что зафиксировано

P2-03 использует отдельный evaluation layer, который принимает точные `NormalizedVacancy`, опциональный `NormalizedResume` и immutable `TargetPolicy`

Основная метрика фильтра — false exclusion rate, потому что P2-03 имеет право только доказанно исключать вакансию и не должен терять потенциально подходящие варианты

## Bootstrap corpus

Первый corpus `filter-bootstrap-v1` содержит 26 adversarial кейсов

- 17 кейсов должны завершаться `KEEP`
- 9 кейсов должны завершаться `EXCLUDE_PROVEN`
- покрыты неоднозначные роли, incidental technologies, C++, Java Backend, remote/location, relocation negation, unknown work format, forbidden context, availability и experience gap

Baseline для текущего P2-03:

- false exclusions: `0 / 17`
- retention false-negative rate: `0.0`
- missed proven exclusions: `0 / 9`
- reason mismatches: `0 / 9`

Этот corpus имеет `release_gate_eligible=false`

Он нужен для bootstrap-регрессий и не заменяет реальную размеченную выборку HH
Production release gate по FNR можно включать только после появления real annotated corpus из фактического discovery потока

## Запуск

```bash
python -m careerops_processing.evaluation \
  --policy-dir config/processing/target_policies \
  --output docs/processing/baselines/filter-bootstrap-v1.json
```

Evaluation command возвращает ненулевой код при false exclusion, missed exclusion или reason mismatch
