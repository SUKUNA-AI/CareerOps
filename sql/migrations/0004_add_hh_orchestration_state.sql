BEGIN;

ALTER TABLE careerops.source_profiles
    ADD COLUMN account_key text;

ALTER TABLE careerops.resumes
    ADD COLUMN upstream_status text,
    ADD COLUMN lifecycle text NOT NULL DEFAULT 'active',
    ADD COLUMN present_in_upstream boolean NOT NULL DEFAULT true,
    ADD COLUMN inactive_at timestamptz,
    ADD COLUMN binding_key text,
    ADD COLUMN binding_version integer,
    ADD COLUMN target_key text,
    ADD COLUMN binding_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN auto_apply boolean NOT NULL DEFAULT false,
    ADD COLUMN selectable_for_evaluation boolean NOT NULL DEFAULT false,
    ADD COLUMN selectable_for_auto_apply boolean NOT NULL DEFAULT false,
    ADD COLUMN query_sets text[] NOT NULL DEFAULT '{}',
    ADD COLUMN source_payload jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE careerops.resumes
    ADD CONSTRAINT resumes_lifecycle_ck
        CHECK (lifecycle IN ('active', 'deleted')),
    ADD CONSTRAINT resumes_binding_version_ck
        CHECK (binding_version IS NULL OR binding_version >= 1),
    ADD CONSTRAINT resumes_lifecycle_state_ck CHECK (
        (
            lifecycle = 'active'
            AND present_in_upstream
            AND inactive_at IS NULL
        )
        OR (
            lifecycle = 'deleted'
            AND NOT present_in_upstream
            AND inactive_at IS NOT NULL
        )
    ),
    ADD CONSTRAINT resumes_evaluation_selection_ck CHECK (
        NOT selectable_for_evaluation
        OR (
            lifecycle = 'active'
            AND present_in_upstream
            AND binding_enabled
            AND binding_key IS NOT NULL
            AND target_key IS NOT NULL
        )
    ),
    ADD CONSTRAINT resumes_auto_apply_selection_ck
        CHECK (
            NOT selectable_for_auto_apply
            OR (
                lifecycle = 'active'
                AND present_in_upstream
                AND binding_enabled
                AND binding_key IS NOT NULL
                AND target_key IS NOT NULL
                AND auto_apply
                AND selectable_for_evaluation
                AND upstream_status = 'published'
            )
        );

CREATE UNIQUE INDEX source_profiles_source_account_uk
    ON careerops.source_profiles (source, account_key)
    WHERE account_key IS NOT NULL;

CREATE TABLE careerops.observe_query_cursors (
    source_profile_id bigint PRIMARY KEY
        REFERENCES careerops.source_profiles (id),
    account_key text NOT NULL,
    catalog_signature text NOT NULL,
    catalog_size integer NOT NULL,
    next_query_offset integer NOT NULL,
    last_window_start integer NOT NULL,
    last_window_size integer NOT NULL,
    last_run_id uuid NOT NULL,
    last_reserved_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT observe_query_cursors_signature_ck CHECK (
        catalog_signature ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT observe_query_cursors_catalog_size_ck CHECK (catalog_size >= 1),
    CONSTRAINT observe_query_cursors_offset_ck CHECK (
        next_query_offset >= 0
        AND next_query_offset < catalog_size
        AND last_window_start >= 0
        AND last_window_start < catalog_size
    ),
    CONSTRAINT observe_query_cursors_window_ck CHECK (
        last_window_size >= 1
        AND last_window_size <= catalog_size
    )
);

CREATE TABLE careerops.observation_runs (
    id uuid PRIMARY KEY,
    source_profile_id bigint NOT NULL
        REFERENCES careerops.source_profiles (id),
    account_key text NOT NULL,
    status text NOT NULL,
    query_set_keys text[] NOT NULL DEFAULT '{}',
    query_keys text[] NOT NULL DEFAULT '{}',
    query_catalog_size integer NOT NULL,
    query_catalog_signature text NOT NULL,
    max_queries_per_run integer NOT NULL,
    query_cursor_start integer NOT NULL,
    query_cursor_next integer NOT NULL,
    query_rotation_wrapped boolean NOT NULL,
    pages integer NOT NULL,
    per_page integer NOT NULL,
    max_unique_vacancies integer NOT NULL,
    max_full_fetches integer NOT NULL,
    search_delay_seconds double precision NOT NULL,
    full_fetch_min_delay_seconds double precision NOT NULL,
    full_fetch_max_delay_seconds double precision NOT NULL,
    search_observation_count integer,
    unique_vacancy_count integer,
    candidate_count integer,
    full_fetch_attempted integer,
    full_fetched integer,
    evaluation_candidate_count integer,
    failed integer,
    stopped_on_captcha boolean,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    s3_prefix text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT observation_runs_status_ck
        CHECK (status IN ('running', 'incomplete', 'finished')),
    CONSTRAINT observation_runs_time_order_ck CHECK (
        finished_at IS NULL OR finished_at >= started_at
    ),
    CONSTRAINT observation_runs_query_rotation_ck CHECK (
        query_catalog_size >= 1
        AND max_queries_per_run >= 1
        AND cardinality(query_keys) >= 1
        AND cardinality(query_keys) <= max_queries_per_run
        AND cardinality(query_keys) <= query_catalog_size
        AND query_cursor_start >= 0
        AND query_cursor_start < query_catalog_size
        AND query_cursor_next >= 0
        AND query_cursor_next < query_catalog_size
        AND query_catalog_signature ~ '^[0-9a-f]{64}$'
    )
);

CREATE TABLE careerops.vacancy_observations (
    run_id uuid NOT NULL REFERENCES careerops.observation_runs (id),
    vacancy_id bigint NOT NULL REFERENCES careerops.vacancies (id),
    full_fetch_status text NOT NULL,
    matched_query_keys text[] NOT NULL DEFAULT '{}',
    matched_query_sets text[] NOT NULL DEFAULT '{}',
    query_page_uris text[] NOT NULL DEFAULT '{}',
    search_item_uri text NOT NULL,
    vacancy_uri text,
    evaluation_candidates_uri text NOT NULL,
    observed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, vacancy_id)
);

CREATE TABLE careerops.evaluation_work_items (
    run_id uuid NOT NULL REFERENCES careerops.observation_runs (id),
    vacancy_id bigint NOT NULL REFERENCES careerops.vacancies (id),
    resume_id bigint NOT NULL REFERENCES careerops.resumes (id),
    binding_key text NOT NULL,
    target_key text NOT NULL,
    binding_version integer NOT NULL,
    auto_apply boolean NOT NULL,
    matched_query_keys text[] NOT NULL DEFAULT '{}',
    matched_query_sets text[] NOT NULL DEFAULT '{}',
    resume_query_sets text[] NOT NULL DEFAULT '{}',
    overlap_query_keys text[] NOT NULL DEFAULT '{}',
    overlap_query_sets text[] NOT NULL DEFAULT '{}',
    has_provenance_overlap boolean NOT NULL,
    full_fetch_status text NOT NULL,
    evaluation_status text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, vacancy_id, resume_id),
    CONSTRAINT evaluation_work_items_binding_version_ck
        CHECK (binding_version >= 1)
);

CREATE INDEX observation_runs_account_started_idx
    ON careerops.observation_runs (account_key, started_at DESC);

CREATE INDEX vacancy_observations_vacancy_idx
    ON careerops.vacancy_observations (vacancy_id, observed_at DESC);

CREATE INDEX evaluation_work_items_resume_status_idx
    ON careerops.evaluation_work_items (resume_id, evaluation_status, created_at DESC);

COMMIT;
