# CareerOPS calibration artifacts

Эта директория хранит только маленькие версионированные manifests и документацию calibration datasets.

Сырые Astra inputs/labels, stage predictions и generated reports в Git не коммитятся.

## Astra calibration-v1

Текущий внешний dataset:

- `1500` vacancy × resume pairs
- `889` уникальных вакансий
- `6` HH resume snapshots
- label tier: `weak_gold`
- policy: `careerops-high-recall-v1`

Source manifest: `manifests/astra-calibration-v1.json`.

Текущий freeze gate заблокирован только из-за отсутствующего точного `annotator_model` в Astra output. Значение нельзя угадывать: перед заморозкой dataset нужно вписать фактическое имя/версию модели и пересоздать prepared manifest.

## Local layout

Рекомендуемый локальный layout, который игнорируется Git:

```text
calibration/
  datasets/
    astra-calibration-v1/
      annotations.jsonl
      astra_batches.jsonl
      prepared/
  predictions/
    astra-calibration-v1/
      p203.jsonl
      p204.jsonl
      p205.jsonl
      p206.jsonl
      p207.jsonl
      p207_replay.jsonl
  reports/
    astra-calibration-v1/
```

## Prepare

```bash
python -m careerops_processing.calibration prepare \
  --annotations calibration/datasets/astra-calibration-v1/annotations.jsonl \
  --batches calibration/datasets/astra-calibration-v1/astra_batches.jsonl \
  --output-dir calibration/datasets/astra-calibration-v1/prepared \
  --dataset-id astra-calibration-v1
```

Если точная Astra model identity известна, добавить:

```text
--annotator-model <exact-model-id>
```

`prepare` валидирует pair coverage, считает hashes и создаёт deterministic vacancy-grouped split `70/15/15`. Все пары одной вакансии всегда находятся только в одном split.

## Evaluate

Stage prediction exports имеют фиксированные имена `p203.jsonl` ... `p207.jsonl`.

```bash
python -m careerops_processing.calibration evaluate \
  --annotations calibration/datasets/astra-calibration-v1/annotations.jsonl \
  --manifest calibration/datasets/astra-calibration-v1/prepared/manifest.json \
  --assignments calibration/datasets/astra-calibration-v1/prepared/split_assignments.jsonl \
  --predictions-dir calibration/predictions/astra-calibration-v1 \
  --split validation \
  --output-dir calibration/reports/astra-calibration-v1/validation
```

Harness публикует `calibration_report.json` и `calibration_report.md`.

## P2-07 search

P2-07 search работает по exported replay cases и candidate policies. Search выполняется на calibration split; validation используется для выбора устойчивого кандидата, holdout остаётся frozen до финальной проверки.

Нельзя подбирать policy на holdout и затем публиковать holdout metric как независимую оценку.
