BEGIN;

CREATE INDEX IF NOT EXISTS batch_runs_started_at_idx
    ON careerops.batch_runs (started_at DESC);

CREATE INDEX IF NOT EXISTS batch_runs_status_started_at_idx
    ON careerops.batch_runs (status, started_at DESC);

CREATE INDEX IF NOT EXISTS vacancies_last_seen_at_idx
    ON careerops.vacancies (last_seen_at DESC);

CREATE INDEX IF NOT EXISTS vacancies_source_employer_idx
    ON careerops.vacancies (source, source_employer_id)
    WHERE source_employer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS vacancy_decisions_run_id_idx
    ON careerops.vacancy_decisions (run_id);

CREATE INDEX IF NOT EXISTS vacancy_decisions_vacancy_created_at_idx
    ON careerops.vacancy_decisions (vacancy_id, created_at DESC);

CREATE INDEX IF NOT EXISTS applications_batch_run_id_idx
    ON careerops.applications (batch_run_id)
    WHERE batch_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS applications_requested_at_idx
    ON careerops.applications (requested_at DESC);

COMMIT;
