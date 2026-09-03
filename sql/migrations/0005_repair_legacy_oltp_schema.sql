BEGIN;

-- Repair CareerOPS installations created from the legacy OLTP schema.
-- Fresh installations already receive these columns from migration 0001,
-- so this migration is intentionally idempotent.

ALTER TABLE careerops.resumes
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE careerops.vacancies
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE careerops.batch_runs
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE careerops.applications
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

-- Legacy deployments had stricter nullability than the current runtime contract.

ALTER TABLE careerops.vacancies
    ALTER COLUMN title DROP NOT NULL;

ALTER TABLE careerops.batch_runs
    ALTER COLUMN discovered DROP NOT NULL,
    ALTER COLUMN prefiltered DROP NOT NULL,
    ALTER COLUMN full_fetched DROP NOT NULL,
    ALTER COLUMN accepted DROP NOT NULL,
    ALTER COLUMN submitted DROP NOT NULL,
    ALTER COLUMN confirmed DROP NOT NULL,
    ALTER COLUMN failed DROP NOT NULL,
    ALTER COLUMN stopped_on_captcha DROP NOT NULL;

COMMIT;
