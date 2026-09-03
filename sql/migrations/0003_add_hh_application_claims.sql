BEGIN;

CREATE TABLE careerops.application_claims (
    id uuid PRIMARY KEY,
    account_key text NOT NULL,
    resume_id bigint NOT NULL REFERENCES careerops.resumes (id),
    vacancy_id bigint NOT NULL REFERENCES careerops.vacancies (id),
    application_run_id uuid NOT NULL,
    status text NOT NULL,
    attempt_count integer NOT NULL DEFAULT 1,
    claimed_at timestamptz NOT NULL,
    state_changed_at timestamptz NOT NULL,
    submitted_at timestamptz,
    finished_at timestamptz,
    last_error_type text,
    last_error_message text,
    upstream_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT application_claims_identity_uk
        UNIQUE (resume_id, vacancy_id),
    CONSTRAINT application_claims_status_ck CHECK (
        status IN (
            'CLAIMED',
            'SUBMITTING',
            'SUBMITTED',
            'UNCERTAIN',
            'FAILED_SAFE_TO_RETRY'
        )
    ),
    CONSTRAINT application_claims_attempt_count_ck CHECK (attempt_count >= 1),
    CONSTRAINT application_claims_time_order_ck CHECK (
        state_changed_at >= claimed_at
        AND (submitted_at IS NULL OR submitted_at >= claimed_at)
        AND (finished_at IS NULL OR finished_at >= claimed_at)
    )
);

CREATE INDEX application_claims_status_changed_idx
    ON careerops.application_claims (status, state_changed_at DESC);

COMMIT;
