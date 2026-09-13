from __future__ import annotations

from pathlib import Path

import pytest

from careerops_integrations.hh.configuration import HHConfigError, load_discovery_config

DISCOVERY_PATH = Path("config/hh_discovery.toml")

RETIRED_DISCOVERY_FIELDS = (
    "pages",
    "max_queries_per_run",
    "search_query_delay_seconds",
    "full_fetch_min_delay_seconds",
    "full_fetch_max_delay_seconds",
    "max_unique_vacancies_per_run",
    "max_full_fetch_per_run",
)


def test_committed_discovery_catalog_has_no_retired_run_budget_fields() -> None:
    text = DISCOVERY_PATH.read_text(encoding="utf-8")
    discovery = load_discovery_config(DISCOVERY_PATH)

    assert len(discovery.query_sets) == 20
    assert sum(discovery.enabled_query_count_by_set.values()) == 388
    for field in RETIRED_DISCOVERY_FIELDS:
        assert f"{field} =" not in text


@pytest.mark.parametrize("field", RETIRED_DISCOVERY_FIELDS)
def test_retired_discovery_default_fields_are_rejected(
    workspace_tmp_dir: Path,
    field: str,
) -> None:
    path = workspace_tmp_dir / "discovery.toml"
    value = "1.0" if "seconds" in field else "1"
    path.write_text(
        f"""schema_version = 1
[defaults]
{field} = {value}
[query_sets.one]
version = 1
queries = [{{ key = "one-query", text = "One", enabled = true }}]
""",
        encoding="utf-8",
    )

    with pytest.raises(HHConfigError, match="extra_forbidden"):
        load_discovery_config(path)


def test_query_level_pages_is_rejected(workspace_tmp_dir: Path) -> None:
    path = workspace_tmp_dir / "discovery.toml"
    path.write_text(
        """schema_version = 1
[query_sets.one]
version = 1
queries = [{ key = "one-query", text = "One", enabled = true, pages = 2 }]
""",
        encoding="utf-8",
    )

    with pytest.raises(HHConfigError, match="extra_forbidden"):
        load_discovery_config(path)
