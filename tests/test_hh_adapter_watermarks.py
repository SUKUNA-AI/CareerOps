from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from careerops_adapter.hh.producer import SourceSeedKind, build_source_generation_plan
from careerops_adapter.hh.worker import _page_watermark_state
from careerops_integrations.hh.configuration import (
    load_accounts_config,
    load_discovery_config,
)


def _item(published_at: str | None) -> dict[str, object]:
    payload: dict[str, object] = {"id": "1"}
    if published_at is not None:
        payload["published_at"] = published_at
    return payload


def test_page_watermark_continues_before_overlap_boundary() -> None:
    previous = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    newest, reached = _page_watermark_state(
        [
            _item("2026-09-06T12:30:00+00:00"),
            _item("2026-09-06T11:30:00+00:00"),
        ],
        previous_watermark=previous,
        overlap_seconds=3600,
    )

    assert newest == datetime(2026, 9, 6, 12, 30, tzinfo=UTC)
    assert reached is False


def test_page_watermark_stops_after_overlap_boundary() -> None:
    previous = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    newest, reached = _page_watermark_state(
        [
            _item("2026-09-06T12:30:00+00:00"),
            _item("2026-09-06T10:59:59+00:00"),
        ],
        previous_watermark=previous,
        overlap_seconds=3600,
    )

    assert newest == datetime(2026, 9, 6, 12, 30, tzinfo=UTC)
    assert reached is True


def test_missing_publication_time_never_proves_watermark_boundary() -> None:
    previous = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    newest, reached = _page_watermark_state(
        [
            _item("2026-09-06T10:00:00+00:00"),
            _item(None),
        ],
        previous_watermark=previous,
        overlap_seconds=3600,
    )

    assert newest == datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
    assert reached is False


def test_generation_root_carries_persistent_watermark_and_not_legacy_page_cap() -> None:
    discovery = load_discovery_config("config/hh_discovery.toml")
    accounts = load_accounts_config(
        "config/hh_accounts.example.toml",
        discovery=discovery,
    )
    account = accounts.resolve_account("junior")
    selected_queries = discovery.select_queries(account.query_set_keys)
    assert selected_queries
    first_query = selected_queries[0].spec.key
    previous = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)

    plan = build_source_generation_plan(
        account=account,
        discovery=discovery,
        generation_id=uuid4(),
        kind=SourceSeedKind.SEARCH,
        watermarks={first_query: previous},
        watermark_overlap_seconds=3600,
    )

    first = next(
        task for task in plan.search_tasks if task.parameters["query_key"] == first_query
    )
    assert first.parameters["page"] == 0
    assert first.parameters["max_pages"] > 2
    assert first.parameters["previous_watermark_at"] == previous.isoformat()
    assert first.parameters["watermark_overlap_seconds"] == 3600
    assert len(plan.search_tasks) == len(selected_queries)
