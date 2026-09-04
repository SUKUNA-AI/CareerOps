from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Numeric, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, DOUBLE_PRECISION, JSONB, TIMESTAMP, UUID
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.schema import ColumnCollectionConstraint, CreateIndex

from careerops_storage import metadata as exported_metadata
from careerops_storage.schema import CAREEROPS_SCHEMA, metadata

EXPECTED_COLUMNS = {
    "source_profiles": (
        "id",
        "source",
        "profile_key",
        "created_at",
        "updated_at",
        "account_key",
    ),
    "resumes": (
        "id",
        "source_profile_id",
        "source_resume_id",
        "title",
        "raw_uri",
        "content_hash",
        "first_seen_at",
        "last_seen_at",
        "created_at",
        "updated_at",
        "upstream_status",
        "lifecycle",
        "present_in_upstream",
        "inactive_at",
        "binding_key",
        "binding_version",
        "target_key",
        "binding_enabled",
        "auto_apply",
        "selectable_for_evaluation",
        "selectable_for_auto_apply",
        "query_sets",
        "source_payload",
    ),
    "vacancies": (
        "id",
        "source",
        "source_entity_id",
        "source_employer_id",
        "title",
        "company_name",
        "description",
        "salary_from",
        "salary_to",
        "salary_currency",
        "location",
        "remote",
        "employment_type",
        "experience",
        "source_url",
        "relations",
        "archived",
        "closed_for_applicants",
        "has_test",
        "response_letter_required",
        "response_url",
        "published_at",
        "first_seen_at",
        "last_seen_at",
        "latest_raw_uri",
        "latest_content_hash",
        "created_at",
        "updated_at",
    ),
    "batch_runs": (
        "id",
        "resume_id",
        "search_query",
        "area_id",
        "period_days",
        "pages",
        "per_page",
        "max_responses",
        "professional_roles",
        "cover_letter_mode",
        "live",
        "status",
        "discovered",
        "prefiltered",
        "full_fetched",
        "accepted",
        "submitted",
        "confirmed",
        "failed",
        "stopped_on_captcha",
        "started_at",
        "finished_at",
        "s3_prefix",
        "created_at",
        "updated_at",
    ),
    "vacancy_decisions": (
        "id",
        "run_id",
        "vacancy_id",
        "stage",
        "accepted",
        "reason",
        "matched_domains",
        "blocked_terms",
        "metadata",
        "created_at",
    ),
    "applications": (
        "id",
        "application_run_id",
        "batch_run_id",
        "vacancy_id",
        "resume_id",
        "submission_mode",
        "status",
        "confirmed",
        "requested_at",
        "finished_at",
        "cover_letter_uri",
        "request_uri",
        "result_uri",
        "before_uri",
        "after_uri",
        "error_type",
        "error_message",
        "upstream_metadata",
        "created_at",
        "updated_at",
    ),
    "application_claims": (
        "id",
        "account_key",
        "resume_id",
        "vacancy_id",
        "application_run_id",
        "status",
        "attempt_count",
        "claimed_at",
        "state_changed_at",
        "submitted_at",
        "finished_at",
        "last_error_type",
        "last_error_message",
        "upstream_evidence",
        "created_at",
        "updated_at",
    ),
    "observe_query_cursors": (
        "source_profile_id",
        "account_key",
        "catalog_signature",
        "catalog_size",
        "next_query_offset",
        "last_window_start",
        "last_window_size",
        "last_run_id",
        "last_reserved_at",
        "created_at",
        "updated_at",
    ),
    "observation_runs": (
        "id",
        "source_profile_id",
        "account_key",
        "status",
        "query_set_keys",
        "query_keys",
        "query_catalog_size",
        "query_catalog_signature",
        "max_queries_per_run",
        "query_cursor_start",
        "query_cursor_next",
        "query_rotation_wrapped",
        "pages",
        "per_page",
        "max_unique_vacancies",
        "max_full_fetches",
        "search_delay_seconds",
        "full_fetch_min_delay_seconds",
        "full_fetch_max_delay_seconds",
        "search_observation_count",
        "unique_vacancy_count",
        "candidate_count",
        "full_fetch_attempted",
        "full_fetched",
        "evaluation_candidate_count",
        "failed",
        "stopped_on_captcha",
        "started_at",
        "finished_at",
        "s3_prefix",
        "created_at",
        "updated_at",
    ),
    "vacancy_observations": (
        "run_id",
        "vacancy_id",
        "full_fetch_status",
        "matched_query_keys",
        "matched_query_sets",
        "query_page_uris",
        "search_item_uri",
        "vacancy_uri",
        "evaluation_candidates_uri",
        "observed_at",
        "created_at",
        "updated_at",
    ),
    "evaluation_work_items": (
        "run_id",
        "vacancy_id",
        "resume_id",
        "binding_key",
        "target_key",
        "binding_version",
        "auto_apply",
        "matched_query_keys",
        "matched_query_sets",
        "resume_query_sets",
        "overlap_query_keys",
        "overlap_query_sets",
        "has_provenance_overlap",
        "full_fetch_status",
        "evaluation_status",
        "created_at",
        "updated_at",
    ),
}

EXPECTED_NULLABLE_COLUMNS = {
    "source_profiles": {"account_key"},
    "resumes": {
        "title",
        "raw_uri",
        "content_hash",
        "upstream_status",
        "inactive_at",
        "binding_key",
        "binding_version",
        "target_key",
    },
    "vacancies": {
        "source_employer_id",
        "title",
        "company_name",
        "description",
        "salary_from",
        "salary_to",
        "salary_currency",
        "location",
        "remote",
        "employment_type",
        "experience",
        "source_url",
        "relations",
        "archived",
        "closed_for_applicants",
        "has_test",
        "response_letter_required",
        "response_url",
        "published_at",
    },
    "batch_runs": {
        "search_query",
        "area_id",
        "period_days",
        "pages",
        "per_page",
        "max_responses",
        "cover_letter_mode",
        "discovered",
        "prefiltered",
        "full_fetched",
        "accepted",
        "submitted",
        "confirmed",
        "failed",
        "stopped_on_captcha",
        "finished_at",
        "s3_prefix",
    },
    "vacancy_decisions": set(),
    "applications": {
        "batch_run_id",
        "confirmed",
        "finished_at",
        "cover_letter_uri",
        "request_uri",
        "result_uri",
        "before_uri",
        "after_uri",
        "error_type",
        "error_message",
    },
    "application_claims": {
        "submitted_at",
        "finished_at",
        "last_error_type",
        "last_error_message",
    },
    "observe_query_cursors": set(),
    "observation_runs": {
        "search_observation_count",
        "unique_vacancy_count",
        "candidate_count",
        "full_fetch_attempted",
        "full_fetched",
        "evaluation_candidate_count",
        "failed",
        "stopped_on_captcha",
        "finished_at",
    },
    "vacancy_observations": {"vacancy_uri"},
    "evaluation_work_items": set(),
}


def _table(name: str) -> Table:
    return metadata.tables[f"{CAREEROPS_SCHEMA}.{name}"]


def _named_constraints(
    constraint_type: type[ColumnCollectionConstraint],
) -> dict[str, tuple[str, tuple[str, ...]]]:
    return {
        str(constraint.name): (table.name, tuple(constraint.columns.keys()))
        for table in metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, constraint_type) and constraint.name is not None
    }


def _normalized_sql(value: object) -> str:
    return " ".join(str(value).split())


def test_package_exports_one_canonical_metadata_with_current_tables() -> None:
    assert exported_metadata is metadata
    assert metadata.schema == CAREEROPS_SCHEMA == "careerops"
    assert set(metadata.tables) == {f"careerops.{table_name}" for table_name in EXPECTED_COLUMNS}

    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        table = _table(table_name)
        assert table.schema == "careerops"
        assert tuple(table.columns.keys()) == expected_columns


def test_effective_nullability_includes_0005_repairs() -> None:
    for table_name, expected_nullable in EXPECTED_NULLABLE_COLUMNS.items():
        actual_nullable = {column.name for column in _table(table_name).columns if column.nullable}
        assert actual_nullable == expected_nullable

    for table_name in ("resumes", "vacancies", "batch_runs", "applications"):
        assert not _table(table_name).c.created_at.nullable
        assert not _table(table_name).c.updated_at.nullable


def test_postgresql_specific_types_and_identity_columns() -> None:
    identity_columns = {
        (table.name, column.name)
        for table in metadata.tables.values()
        for column in table.columns
        if column.identity is not None
    }
    assert identity_columns == {
        ("source_profiles", "id"),
        ("resumes", "id"),
        ("vacancies", "id"),
        ("vacancy_decisions", "id"),
        ("applications", "id"),
    }
    for table_name, column_name in identity_columns:
        column = _table(table_name).c[column_name]
        assert isinstance(column.type, BigInteger)
        assert column.identity is not None
        assert column.identity.always is False

    expected_uuid_columns = {
        ("batch_runs", "id"),
        ("vacancy_decisions", "run_id"),
        ("applications", "application_run_id"),
        ("applications", "batch_run_id"),
        ("application_claims", "id"),
        ("application_claims", "application_run_id"),
        ("observe_query_cursors", "last_run_id"),
        ("observation_runs", "id"),
        ("vacancy_observations", "run_id"),
        ("evaluation_work_items", "run_id"),
    }
    actual_uuid_columns = {
        (table.name, column.name)
        for table in metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, UUID)
    }
    assert actual_uuid_columns == expected_uuid_columns
    assert all(_table(table).c[column].type.as_uuid for table, column in actual_uuid_columns)

    expected_array_columns = {
        ("resumes", "query_sets"),
        ("vacancies", "relations"),
        ("batch_runs", "professional_roles"),
        ("vacancy_decisions", "matched_domains"),
        ("vacancy_decisions", "blocked_terms"),
        ("observation_runs", "query_set_keys"),
        ("observation_runs", "query_keys"),
        ("vacancy_observations", "matched_query_keys"),
        ("vacancy_observations", "matched_query_sets"),
        ("vacancy_observations", "query_page_uris"),
        ("evaluation_work_items", "matched_query_keys"),
        ("evaluation_work_items", "matched_query_sets"),
        ("evaluation_work_items", "resume_query_sets"),
        ("evaluation_work_items", "overlap_query_keys"),
        ("evaluation_work_items", "overlap_query_sets"),
    }
    actual_array_columns = {
        (table.name, column.name)
        for table in metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, ARRAY)
    }
    assert actual_array_columns == expected_array_columns

    expected_jsonb_columns = {
        ("resumes", "source_payload"),
        ("vacancy_decisions", "metadata"),
        ("applications", "upstream_metadata"),
        ("application_claims", "upstream_evidence"),
    }
    actual_jsonb_columns = {
        (table.name, column.name)
        for table in metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, JSONB)
    }
    assert actual_jsonb_columns == expected_jsonb_columns

    assert isinstance(_table("vacancies").c.salary_from.type, Numeric)
    assert isinstance(_table("vacancies").c.salary_to.type, Numeric)
    for column_name in (
        "search_delay_seconds",
        "full_fetch_min_delay_seconds",
        "full_fetch_max_delay_seconds",
    ):
        assert isinstance(_table("observation_runs").c[column_name].type, DOUBLE_PRECISION)

    timestamp_columns = [
        column
        for table in metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, TIMESTAMP)
    ]
    assert timestamp_columns
    assert all(column.type.timezone for column in timestamp_columns)


def test_primary_keys_foreign_keys_and_unique_constraints() -> None:
    actual_primary_keys = {
        table.name: tuple(table.primary_key.columns.keys()) for table in metadata.tables.values()
    }
    assert actual_primary_keys == {
        "source_profiles": ("id",),
        "resumes": ("id",),
        "vacancies": ("id",),
        "batch_runs": ("id",),
        "vacancy_decisions": ("id",),
        "applications": ("id",),
        "application_claims": ("id",),
        "observe_query_cursors": ("source_profile_id",),
        "observation_runs": ("id",),
        "vacancy_observations": ("run_id", "vacancy_id"),
        "evaluation_work_items": ("run_id", "vacancy_id", "resume_id"),
    }

    actual_foreign_keys = {
        (table.name, column.name, foreign_key.target_fullname)
        for table in metadata.tables.values()
        for column in table.columns
        for foreign_key in column.foreign_keys
    }
    assert actual_foreign_keys == {
        ("resumes", "source_profile_id", "careerops.source_profiles.id"),
        ("batch_runs", "resume_id", "careerops.resumes.id"),
        ("vacancy_decisions", "run_id", "careerops.batch_runs.id"),
        ("vacancy_decisions", "vacancy_id", "careerops.vacancies.id"),
        ("applications", "batch_run_id", "careerops.batch_runs.id"),
        ("applications", "vacancy_id", "careerops.vacancies.id"),
        ("applications", "resume_id", "careerops.resumes.id"),
        ("application_claims", "resume_id", "careerops.resumes.id"),
        ("application_claims", "vacancy_id", "careerops.vacancies.id"),
        ("observe_query_cursors", "source_profile_id", "careerops.source_profiles.id"),
        ("observation_runs", "source_profile_id", "careerops.source_profiles.id"),
        ("vacancy_observations", "run_id", "careerops.observation_runs.id"),
        ("vacancy_observations", "vacancy_id", "careerops.vacancies.id"),
        ("evaluation_work_items", "run_id", "careerops.observation_runs.id"),
        ("evaluation_work_items", "vacancy_id", "careerops.vacancies.id"),
        ("evaluation_work_items", "resume_id", "careerops.resumes.id"),
    }

    assert _named_constraints(UniqueConstraint) == {
        "source_profiles_source_profile_key_uk": ("source_profiles", ("source", "profile_key")),
        "resumes_source_profile_resume_uk": (
            "resumes",
            ("source_profile_id", "source_resume_id"),
        ),
        "vacancies_source_entity_uk": ("vacancies", ("source", "source_entity_id")),
        "vacancy_decisions_run_vacancy_stage_uk": (
            "vacancy_decisions",
            ("run_id", "vacancy_id", "stage"),
        ),
        "applications_application_run_uk": ("applications", ("application_run_id",)),
        "applications_vacancy_resume_uk": (
            "applications",
            ("vacancy_id", "resume_id"),
        ),
        "application_claims_identity_uk": (
            "application_claims",
            ("resume_id", "vacancy_id"),
        ),
    }


def test_check_constraints_preserve_current_runtime_invariants() -> None:
    checks = _named_constraints(CheckConstraint)
    assert set(checks) == {
        "resumes_seen_order_ck",
        "resumes_content_hash_ck",
        "resumes_lifecycle_ck",
        "resumes_binding_version_ck",
        "resumes_lifecycle_state_ck",
        "resumes_evaluation_selection_ck",
        "resumes_auto_apply_selection_ck",
        "vacancies_seen_order_ck",
        "vacancies_latest_content_hash_ck",
        "batch_runs_status_ck",
        "batch_runs_time_order_ck",
        "batch_runs_finished_at_ck",
        "batch_runs_counters_ck",
        "applications_time_order_ck",
        "application_claims_status_ck",
        "application_claims_attempt_count_ck",
        "application_claims_time_order_ck",
        "observe_query_cursors_signature_ck",
        "observe_query_cursors_catalog_size_ck",
        "observe_query_cursors_offset_ck",
        "observe_query_cursors_window_ck",
        "observation_runs_status_ck",
        "observation_runs_time_order_ck",
        "observation_runs_query_rotation_ck",
        "evaluation_work_items_binding_version_ck",
    }

    sql_by_name = {
        constraint.name: _normalized_sql(constraint.sqltext)
        for table in metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "status IN ('running', 'incomplete', 'finished')" in sql_by_name["batch_runs_status_ck"]
    assert "discovered IS NULL OR discovered >= 0" in sql_by_name["batch_runs_counters_ck"]
    assert "FAILED_SAFE_TO_RETRY" in sql_by_name["application_claims_status_ck"]
    assert (
        "cardinality(query_keys) <= query_catalog_size"
        in sql_by_name["observation_runs_query_rotation_ck"]
    )
    assert "upstream_status = 'published'" in sql_by_name["resumes_auto_apply_selection_ck"]


def test_server_defaults_match_effective_postgresql_defaults() -> None:
    actual_defaults = {
        (table.name, column.name): _normalized_sql(column.server_default.arg)
        for table in metadata.tables.values()
        for column in table.columns
        if column.server_default is not None and column.identity is None
    }
    assert actual_defaults == {
        ("source_profiles", "created_at"): "now()",
        ("source_profiles", "updated_at"): "now()",
        ("resumes", "created_at"): "now()",
        ("resumes", "updated_at"): "now()",
        ("resumes", "lifecycle"): "'active'::text",
        ("resumes", "present_in_upstream"): "true",
        ("resumes", "binding_enabled"): "false",
        ("resumes", "auto_apply"): "false",
        ("resumes", "selectable_for_evaluation"): "false",
        ("resumes", "selectable_for_auto_apply"): "false",
        ("resumes", "query_sets"): "'{}'::text[]",
        ("resumes", "source_payload"): "'{}'::jsonb",
        ("vacancies", "created_at"): "now()",
        ("vacancies", "updated_at"): "now()",
        ("batch_runs", "professional_roles"): "'{}'::text[]",
        ("batch_runs", "created_at"): "now()",
        ("batch_runs", "updated_at"): "now()",
        ("vacancy_decisions", "matched_domains"): "'{}'::text[]",
        ("vacancy_decisions", "blocked_terms"): "'{}'::text[]",
        ("vacancy_decisions", "metadata"): "'{}'::jsonb",
        ("applications", "upstream_metadata"): "'{}'::jsonb",
        ("applications", "created_at"): "now()",
        ("applications", "updated_at"): "now()",
        ("application_claims", "attempt_count"): "1",
        ("application_claims", "upstream_evidence"): "'{}'::jsonb",
        ("application_claims", "created_at"): "now()",
        ("application_claims", "updated_at"): "now()",
        ("observe_query_cursors", "created_at"): "now()",
        ("observe_query_cursors", "updated_at"): "now()",
        ("observation_runs", "query_set_keys"): "'{}'::text[]",
        ("observation_runs", "query_keys"): "'{}'::text[]",
        ("observation_runs", "created_at"): "now()",
        ("observation_runs", "updated_at"): "now()",
        ("vacancy_observations", "matched_query_keys"): "'{}'::text[]",
        ("vacancy_observations", "matched_query_sets"): "'{}'::text[]",
        ("vacancy_observations", "query_page_uris"): "'{}'::text[]",
        ("vacancy_observations", "created_at"): "now()",
        ("vacancy_observations", "updated_at"): "now()",
        ("evaluation_work_items", "matched_query_keys"): "'{}'::text[]",
        ("evaluation_work_items", "matched_query_sets"): "'{}'::text[]",
        ("evaluation_work_items", "resume_query_sets"): "'{}'::text[]",
        ("evaluation_work_items", "overlap_query_keys"): "'{}'::text[]",
        ("evaluation_work_items", "overlap_query_sets"): "'{}'::text[]",
        ("evaluation_work_items", "updated_at"): "now()",
    }


def test_explicit_indexes_include_sort_direction_and_partial_predicates() -> None:
    indexes = {index.name: index for table in metadata.tables.values() for index in table.indexes}
    assert {
        name: (index.table.name, tuple(index.columns.keys()), index.unique)
        for name, index in indexes.items()
    } == {
        "batch_runs_started_at_idx": ("batch_runs", ("started_at",), False),
        "batch_runs_status_started_at_idx": (
            "batch_runs",
            ("status", "started_at"),
            False,
        ),
        "vacancies_last_seen_at_idx": ("vacancies", ("last_seen_at",), False),
        "vacancies_source_employer_idx": (
            "vacancies",
            ("source", "source_employer_id"),
            False,
        ),
        "vacancy_decisions_run_id_idx": ("vacancy_decisions", ("run_id",), False),
        "vacancy_decisions_vacancy_created_at_idx": (
            "vacancy_decisions",
            ("vacancy_id", "created_at"),
            False,
        ),
        "applications_batch_run_id_idx": ("applications", ("batch_run_id",), False),
        "applications_requested_at_idx": ("applications", ("requested_at",), False),
        "application_claims_status_changed_idx": (
            "application_claims",
            ("status", "state_changed_at"),
            False,
        ),
        "source_profiles_source_account_uk": (
            "source_profiles",
            ("source", "account_key"),
            True,
        ),
        "observation_runs_account_started_idx": (
            "observation_runs",
            ("account_key", "started_at"),
            False,
        ),
        "vacancy_observations_vacancy_idx": (
            "vacancy_observations",
            ("vacancy_id", "observed_at"),
            False,
        ),
        "evaluation_work_items_resume_status_idx": (
            "evaluation_work_items",
            ("resume_id", "evaluation_status", "created_at"),
            False,
        ),
    }

    index_ddl = {
        name: _normalized_sql(CreateIndex(index).compile(dialect=postgresql_dialect()))
        for name, index in indexes.items()
    }
    descending_indexes = {name for name, ddl in index_ddl.items() if " DESC" in ddl}
    assert descending_indexes == {
        "batch_runs_started_at_idx",
        "batch_runs_status_started_at_idx",
        "vacancies_last_seen_at_idx",
        "vacancy_decisions_vacancy_created_at_idx",
        "applications_requested_at_idx",
        "application_claims_status_changed_idx",
        "observation_runs_account_started_idx",
        "vacancy_observations_vacancy_idx",
        "evaluation_work_items_resume_status_idx",
    }

    partial_indexes = {
        name
        for name, index in indexes.items()
        if index.dialect_options["postgresql"]["where"] is not None
    }
    assert partial_indexes == {
        "vacancies_source_employer_idx",
        "applications_batch_run_id_idx",
        "source_profiles_source_account_uk",
    }
    assert index_ddl["vacancies_source_employer_idx"].endswith(
        "WHERE source_employer_id IS NOT NULL"
    )
    assert index_ddl["applications_batch_run_id_idx"].endswith("WHERE batch_run_id IS NOT NULL")
    assert index_ddl["source_profiles_source_account_uk"].endswith("WHERE account_key IS NOT NULL")
