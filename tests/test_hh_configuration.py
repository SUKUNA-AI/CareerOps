from __future__ import annotations

from pathlib import Path

import pytest

from careerops_integrations.hh.configuration import (
    HHConfigError,
    load_accounts_config,
    load_discovery_config,
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
