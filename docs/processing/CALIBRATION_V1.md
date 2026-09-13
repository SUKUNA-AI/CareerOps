# Processing v2 — Calibration v1

## Назначение

Calibration v1 измеряет и настраивает P2-03 ... P2-07 по внешнему weak-gold набору Astra, не меняя архитектурные границы Processing.

Dataset не является абсолютным ground truth. `annotation_source=astra`, `label_tier=weak_gold`. Позже его должны дополнять или вытеснять реальные application outcomes, recruiter responses, interviews, rejections и explicit manual overrides.

## Split policy

Split строится только по `source_vacancy_id`:

- calibration: 70%
- validation: 15%
- holdout: 15%

Все vacancy × resume пары одной вакансии обязаны находиться в одном split. Random pair-level split запрещён из-за leakage.

Calibration split используется для поиска thresholds/weights. Validation используется для выбора policy и обнаружения overfit. Holdout нельзя использовать при подборе policy; он открывается только для финальной независимой оценки calibration release candidate.

## P2-03

High-recall objective:

- `APPLICATION_CANDIDATE` и `REVIEW` обязаны выживать deterministic prefilter;
- Astra `SKIP` считается expected `EXCLUDE_PROVEN` только при наличии explicit `hard_reject_reasons`;
- primary metric: `retention_false_negative_rate`;
- secondary: retention recall, exclusion precision/recall.

Стоимость false exclusion выше стоимости лишнего retained pair.

## P2-04

Astra requirement list используется как weak-gold requirement inventory.

Prediction export содержит explicit `gold_requirement_index`. Alignment не угадывается evaluator'ом: отдельный calibration runner обязан зафиксировать сопоставление system requirement -> Astra requirement, чтобы метрика не зависела от скрытой fuzzy эвристики.

Metrics:

- requirement recall/precision;
- recall significant requirements (`MANDATORY`, `PREFERRED`);
- evidence ref recall/precision;
- duplicate alignments.

## P2-05

Ranking quality считается только для requirements, где Astra указала хотя бы один evidence ref.

Requirements без gold evidence не входят в ranking denominator.

Primary metrics:

- Recall@1/3/5
- MRR
- NDCG@5

Diagnostic:

- Precision@5
- MAP@5

Current Astra labels содержат binary evidence membership. NDCG готов принимать graded relevance в будущей версии dataset.

## P2-06

Astra coarse mapping для calibration-v1:

```text
SUPPORTED   -> MATCHED
PARTIAL     -> MATCHED
UNSUPPORTED -> NOT_EVIDENCED
UNKNOWN     -> UNKNOWN
CONTRADICTED -> CONTRADICTED
```

Report показывает coverage, accuracy on evaluated requirements, confusion matrix и per-state recall.

Классы, отсутствующие в weak-gold dataset, получают `null` recall и не должны искусственно участвовать в macro average.

## P2-07

Policy search replay повторяет production bounded logic:

1. explicit critical prohibited contradiction -> `SKIP`;
2. если `mandatory_support.upper < mandatory_min_support` или `score.upper < candidate_min_score` -> `SKIP`;
3. если lower bounds проходят оба threshold -> `APPLICATION_CANDIDATE`;
4. иначе -> `REVIEW`.

Missing positive-weight component replay'ится как interval `0..100`, то есть отсутствие сигнала не приводит к renormalization известных компонентов.

Search candidate включает:

- `candidate_min_score`;
- `mandatory_min_support`;
- полный набор component weights.

Default search ranking использует high-recall asymmetric cost:

```text
Gold APPLY -> predicted SKIP   = 10
Gold APPLY -> predicted REVIEW = 2
Gold REVIEW -> predicted SKIP  = 3
Gold REVIEW -> predicted APPLY = 1
Gold SKIP -> predicted APPLY   = 2
Gold SKIP -> predicted REVIEW  = 0.5
```

При равной cost выше ставится candidate с большим APPLICATION_CANDIDATE recall, затем с меньшим REVIEW rate.

## Release gate

Calibration release candidate не считается frozen, если:

- Astra annotator model/version неизвестны;
- input hashes не совпадают с manifest;
- есть vacancy leakage между split'ами;
- holdout использовался для policy search;
- stage prediction coverage недостаточна для заявленной метрики.

После выбора policy должны быть зафиксированы `calibration_version`, policy weights/thresholds и итоговый holdout report.
