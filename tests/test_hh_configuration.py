from __future__ import annotations

from pathlib import Path

import pytest

from careerops_integrations.hh.configuration import (
    HHConfigError,
    load_accounts_config,
    load_discovery_config,
)

DISCOVERY_PATH = Path("config/hh_discovery.toml")
ACCOUNTS_PATH = Path("config/hh_accounts.example.toml")


def test_committed_catalog_and_n_account_n_binding_topology_load() -> None:
    discovery = load_discovery_config(DISCOVERY_PATH)
    accounts = load_accounts_config(ACCOUNTS_PATH, discovery=discovery)

    assert len(discovery.query_sets) == 20
    assert sum(discovery.enabled_query_count_by_set.values()) == 388
    assert [account.key for account in accounts.enabled_accounts] == [
        "ml_3y",
        "ml_5y",
        "junior",
    ]
    junior = accounts.resolve_account("junior")
    assert [binding.key for binding in junior.enabled_bindings] == [
    "de_junior",
    "backend_junior",
    "ml_ds_junior",
    "cpp_junior",
    ]

    assert len(junior.query_set_keys) == 15
    assert discovery.defaults.pages == 1
    assert discovery.defaults.per_page == 50
    assert discovery.defaults.max_queries_per_run == 50
    assert discovery.defaults.max_unique_vacancies_per_run == 250
    assert discovery.defaults.max_full_fetch_per_run == 100
    assert discovery.defaults.search_query_delay_seconds == 1.0
    assert discovery.defaults.full_fetch_min_delay_seconds == 1.5
    assert discovery.defaults.full_fetch_max_delay_seconds == 3.0
    assert junior.apply_runs_per_day * junior.max_apply_per_run >= junior.apply_daily_cap


def test_duplicate_query_set_reference_executes_once_per_account_union() -> None:
    discovery = load_discovery_config(DISCOVERY_PATH)
    once = discovery.select_queries(["ml_core"])
    duplicated = discovery.select_queries(["ml_core", "ml_core"])
    assert duplicated == once


def test_catalog_contains_required_broad_ru_en_families() -> None:
    discovery = load_discovery_config(DISCOVERY_PATH)
    texts = {
        query.text
        for query_set in discovery.query_sets.values()
        for query in query_set.queries
    }
    required = {
        "ML Engineer",
        "ML-инженер",
        "Data Scientist",
        "Дата сайентист",
        "LLM Engineer",
        "NLP-инженер",
        "VLM Engineer",
        "OCR Developer",
        "AI Platform Engineer",
        "ML Infrastructure Engineer",
        "Senior ML Engineer",
        "Tech Lead ML",
        "Тимлид Data Science",
        "Data Engineer",
        "ETL Developer",
        "DWH-разработчик",
        "Kafka Engineer",
        "Data Analyst ClickHouse",
        "Python Backend Developer",
        "FastAPI Developer",
        "Django Developer",
        "Flask Developer",
        "Junior Python Developer",
        "Стажёр машинного обучения",
        "C++ Developer",
        "C++ разработчик",
        "Junior C++ Developer",
        "Стажёр C++",
    }
    assert required <= texts


def test_example_has_only_placeholders_and_no_credential_fields() -> None:
    text = ACCOUNTS_PATH.read_text(encoding="utf-8")
    accounts = load_accounts_config(
        ACCOUNTS_PATH,
        discovery=load_discovery_config(DISCOVERY_PATH),
    )
    source_ids = [
        binding.source_resume_id
        for account in accounts.accounts
        for binding in account.bindings
    ]
    assert all(source_id.startswith("REPLACE_ME_") for source_id in source_ids)
    for forbidden in ("token", "cookie", "password", "secret", "access_key"):
        assert forbidden not in text.lower()
    assert all(
        not binding.auto_apply
        for account in accounts.accounts
        for binding in account.bindings
    )


def _write_minimal_discovery(path: Path) -> None:
    path.write_text(
        """schema_version = 1
[query_sets.one]
version = 1
queries = [{ key = "one-query", text = "One", enabled = true }]
""",
        encoding="utf-8",
    )


def test_disabled_accounts_and_bindings_are_ignored(workspace_tmp_dir: Path) -> None:
    discovery_path = workspace_tmp_dir / "discovery.toml"
    accounts_path = workspace_tmp_dir / "accounts.toml"
    _write_minimal_discovery(discovery_path)
    accounts_path.write_text(
        """schema_version = 1
runtime_mode = "observe"
[[accounts]]
key = "active"
profile = "profile-active"
enabled = true
observe_runs_per_day = 1
apply_daily_cap = 100
[[accounts.bindings]]
key = "active"
source_resume_id = "resume-active"
target_key = "target-active"
enabled = true
query_sets = ["one"]
[[accounts.bindings]]
key = "disabled"
source_resume_id = "resume-disabled"
target_key = "target-disabled"
enabled = false
query_sets = ["one"]
[[accounts]]
key = "disabled"
profile = "profile-disabled"
enabled = false
observe_runs_per_day = 1
apply_daily_cap = 100
[[accounts.bindings]]
key = "only"
source_resume_id = "resume-only"
target_key = "target-only"
query_sets = ["one"]
""",
        encoding="utf-8",
    )
    discovery = load_discovery_config(discovery_path)
    accounts = load_accounts_config(accounts_path, discovery=discovery)
    assert [account.key for account in accounts.enabled_accounts] == ["active"]
    assert [binding.key for binding in accounts.enabled_accounts[0].enabled_bindings] == [
        "active"
    ]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            """schema_version = 1
[[accounts]]
key = "dup"
profile = "one"
[[accounts.bindings]]
key = "one"
source_resume_id = "r1"
target_key = "t1"
query_sets = ["one"]
[[accounts]]
key = "dup"
profile = "two"
[[accounts.bindings]]
key = "two"
source_resume_id = "r2"
target_key = "t2"
query_sets = ["one"]
""",
            "duplicate account keys",
        ),
        (
            """schema_version = 1
[[accounts]]
key = "account"
profile = "profile"
[[accounts.bindings]]
key = "dup"
source_resume_id = "r1"
target_key = "t1"
query_sets = ["one"]
[[accounts.bindings]]
key = "dup"
source_resume_id = "r2"
target_key = "t2"
query_sets = ["one"]
""",
            "duplicate resume keys",
        ),
        (
            """schema_version = 1
[[accounts]]
key = "one"
profile = "shared"
[[accounts.bindings]]
key = "one"
source_resume_id = "r1"
target_key = "t1"
query_sets = ["one"]
[[accounts]]
key = "two"
profile = "shared"
[[accounts.bindings]]
key = "two"
source_resume_id = "r2"
target_key = "t2"
query_sets = ["one"]
""",
            "duplicate account profiles",
        ),
    ],
)
def test_duplicate_keys_are_rejected(
    workspace_tmp_dir: Path,
    body: str,
    message: str,
) -> None:
    path = workspace_tmp_dir / "accounts.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(HHConfigError, match=message):
        load_accounts_config(path)


def test_unknown_query_set_and_malformed_toml_are_rejected(
    workspace_tmp_dir: Path,
) -> None:
    discovery_path = workspace_tmp_dir / "discovery.toml"
    _write_minimal_discovery(discovery_path)
    accounts_path = workspace_tmp_dir / "accounts.toml"
    accounts_path.write_text(
        """schema_version = 1
[[accounts]]
key = "account"
profile = "profile"
[[accounts.bindings]]
key = "binding"
source_resume_id = "resume"
target_key = "target"
query_sets = ["missing"]
""",
        encoding="utf-8",
    )
    with pytest.raises(HHConfigError, match="unknown discovery query sets"):
        load_accounts_config(
            accounts_path,
            discovery=load_discovery_config(discovery_path),
        )

    malformed = workspace_tmp_dir / "malformed.toml"
    malformed.write_text("[[not valid", encoding="utf-8")
    with pytest.raises(HHConfigError, match="could not load HH TOML"):
        load_accounts_config(malformed)


def test_credentials_are_not_supported_by_strict_schema(
    workspace_tmp_dir: Path,
) -> None:
    path = workspace_tmp_dir / "accounts.toml"
    path.write_text(
        """schema_version = 1
token = "must-not-be-supported"
[[accounts]]
key = "account"
profile = "profile"
[[accounts.bindings]]
key = "binding"
source_resume_id = "resume"
target_key = "target"
query_sets = ["one"]
""",
        encoding="utf-8",
    )
    with pytest.raises(HHConfigError, match="extra_forbidden"):
        load_accounts_config(path)


def test_apply_schedule_must_have_capacity_to_reach_daily_cap(
    workspace_tmp_dir: Path,
) -> None:
    path = workspace_tmp_dir / "accounts.toml"
    path.write_text(
        """schema_version = 1
[[accounts]]
key = "account"
profile = "profile"
apply_runs_per_day = 3
apply_daily_cap = 100
max_apply_per_run = 15
[[accounts.bindings]]
key = "binding"
source_resume_id = "resume"
target_key = "target"
query_sets = ["one"]
""",
        encoding="utf-8",
    )

    with pytest.raises(
        HHConfigError,
        match=r"apply_runs_per_day \* max_apply_per_run must be >= apply_daily_cap",
    ):
        load_accounts_config(path)
