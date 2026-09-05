from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from careerops_integrations.hh.apply_batch import _require_published_resume
from careerops_integrations.hh.configuration import HHAccountConfig
from careerops_integrations.hh.resume_sync import (
    AccountResumeInventory,
    JsonResumeRegistry,
    ResumeLifecycle,
    reconcile_account_resumes,
    resume_vacancy_dedup_key,
)

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize("status", ["not_published", "draft", None])
def test_apply_rejects_every_non_published_upstream_resume(
    status: str | None,
) -> None:
    payload: dict[str, Any] = {"id": "resume-1"}
    if status is not None:
        payload["status"] = {"id": status}

    with pytest.raises(ValueError, match="not currently published"):
        _require_published_resume(payload, resume_id="resume-1")


def test_apply_accepts_explicitly_published_upstream_resume() -> None:
    _require_published_resume(
        {"id": "resume-1", "status": {"id": "published"}},
        resume_id="resume-1",
    )


class MemoryRegistry:
    def __init__(self) -> None:
        self.inventory: AccountResumeInventory | None = None
        self.save_count = 0

    async def load(
        self,
        *,
        account_key: str,
        source_profile: str,
    ) -> AccountResumeInventory | None:
        return self.inventory

    async def save(self, inventory: AccountResumeInventory) -> None:
        self.inventory = inventory
        self.save_count += 1


class FakeDriver:
    def __init__(
        self,
        items: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.items = items or []
        self.error = error

    def list_resumes(self) -> list[dict[str, Any]]:
        if self.error is not None:
            raise self.error
        return self.items


def _account(*, two_bindings: bool = False) -> HHAccountConfig:
    bindings: list[dict[str, Any]] = [
        {
            "key": "ml",
            "source_resume_id": "resume-1",
            "target_key": "ml_target",
            "enabled": True,
            "auto_apply": True,
            "binding_version": 7,
            "query_sets": ["ml_core"],
        }
    ]
    if two_bindings:
        bindings.append(
            {
                "key": "backend",
                "source_resume_id": "resume-2",
                "target_key": "backend_target",
                "enabled": True,
                "auto_apply": True,
                "binding_version": 3,
                "query_sets": ["python_backend_core"],
            }
        )
    return HHAccountConfig.model_validate(
        {
            "key": "account",
            "profile": "profile",
            "enabled": True,
            "observe_runs_per_day": 3,
            "apply_daily_cap": 100,
            "bindings": bindings,
        }
    )


def _resume(source_id: str, title: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": source_id,
        "title": title,
        "status": {"id": "published"},
        **extra,
    }


def _sync(
    registry: MemoryRegistry,
    items: list[dict[str, Any]],
    *,
    account: HHAccountConfig | None = None,
    observed_at: datetime = NOW,
):
    return asyncio.run(
        reconcile_account_resumes(
            driver=FakeDriver(items),
            account=account or _account(),
            registry=registry,
            observed_at=observed_at,
        )
    )


def test_existing_resume_remains_active() -> None:
    registry = MemoryRegistry()
    first = _sync(registry, [_resume("resume-1", "ML Engineer")])
    second = _sync(
        registry,
        [_resume("resume-1", "ML Engineer")],
        observed_at=NOW + timedelta(hours=1),
    )
    resume = second.inventory.resumes[0]
    assert resume.lifecycle is ResumeLifecycle.ACTIVE
    assert resume.first_seen_at == first.inventory.resumes[0].first_seen_at
    assert resume.last_seen_at == NOW + timedelta(hours=1)
    assert second.deleted_ids == ()


def test_title_and_content_change_keep_identity_and_binding() -> None:
    registry = MemoryRegistry()
    first = _sync(
        registry,
        [_resume("resume-1", "Old title", skills=["Python"])],
    )
    second = _sync(
        registry,
        [_resume("resume-1", "New title", skills=["Python", "SQL"])],
        observed_at=NOW + timedelta(days=1),
    )
    before = first.inventory.resumes[0]
    after = second.inventory.resumes[0]
    assert after.source_resume_id == before.source_resume_id == "resume-1"
    assert after.current_title == "New title"
    assert after.content_sha256 != before.content_sha256
    assert after.binding_key == "ml"
    assert after.target_key == "ml_target"
    assert after.binding_version == 7
    assert second.updated_ids == ("resume-1",)


def test_new_resume_is_registered_unassigned_and_not_auto_applied() -> None:
    registry = MemoryRegistry()
    result = _sync(
        registry,
        [
            _resume("resume-1", "Configured"),
            _resume("resume-new", "Draft test resume"),
        ],
    )
    new_resume = result.inventory.by_source_id["resume-new"]
    assert result.registered_ids == ("resume-1", "resume-new")
    assert new_resume.lifecycle is ResumeLifecycle.ACTIVE
    assert new_resume.binding_key is None
    assert new_resume.target_key is None
    assert new_resume.auto_apply is False
    assert new_resume not in result.inventory.evaluation_resumes
    assert new_resume not in result.inventory.auto_apply_resumes


def test_missing_known_resume_is_marked_deleted_and_history_is_retained() -> None:
    registry = MemoryRegistry()
    first = _sync(registry, [_resume("resume-1", "ML")])
    second = _sync(registry, [], observed_at=NOW + timedelta(days=1))
    deleted = second.inventory.by_source_id["resume-1"]
    assert second.deleted_ids == ("resume-1",)
    assert deleted.lifecycle is ResumeLifecycle.DELETED
    assert deleted.inactive_at == NOW + timedelta(days=1)
    assert deleted.first_seen_at == first.inventory.resumes[0].first_seen_at
    assert deleted.binding_key == "ml"
    assert deleted.binding_enabled is False
    assert deleted.auto_apply is False
    assert second.inventory.auto_apply_resumes == ()
    assert second.inventory.evaluation_resumes == ()


def test_new_id_with_same_title_does_not_inherit_deleted_binding() -> None:
    registry = MemoryRegistry()
    _sync(registry, [_resume("resume-1", "Same title")])
    result = _sync(
        registry,
        [_resume("resume-replacement", "Same title")],
        observed_at=NOW + timedelta(days=1),
    )
    old = result.inventory.by_source_id["resume-1"]
    replacement = result.inventory.by_source_id["resume-replacement"]
    assert old.lifecycle is ResumeLifecycle.DELETED
    assert replacement.lifecycle is ResumeLifecycle.ACTIVE
    assert replacement.binding_key is None
    assert replacement.auto_apply is False


def test_multiple_active_resumes_remain_independently_selectable() -> None:
    registry = MemoryRegistry()
    result = _sync(
        registry,
        [_resume("resume-1", "ML"), _resume("resume-2", "Backend")],
        account=_account(two_bindings=True),
    )
    assert [r.source_resume_id for r in result.inventory.evaluation_resumes] == [
        "resume-1",
        "resume-2",
    ]
    assert [r.binding_key for r in result.inventory.auto_apply_resumes] == [
        "ml",
        "backend",
    ]


def test_duplicate_identity_is_resume_plus_vacancy_not_global_vacancy() -> None:
    first = resume_vacancy_dedup_key("resume-1", "vacancy-7")
    second = resume_vacancy_dedup_key("resume-2", "vacancy-7")
    assert first != second
    assert {first, second} == {
        ("resume-1", "vacancy-7"),
        ("resume-2", "vacancy-7"),
    }


def test_reconciliation_is_idempotent() -> None:
    registry = MemoryRegistry()
    first = _sync(registry, [_resume("resume-1", "ML")])
    second = _sync(registry, [_resume("resume-1", "ML")])
    assert second.inventory == first.inventory
    assert second.registered_ids == ()
    assert second.updated_ids == ()
    assert second.deleted_ids == ()


def test_transport_failure_aborts_without_marking_resumes_deleted() -> None:
    registry = MemoryRegistry()
    initial = _sync(registry, [_resume("resume-1", "ML")])
    save_count = registry.save_count
    with pytest.raises(RuntimeError, match="temporary network failure"):
        asyncio.run(
            reconcile_account_resumes(
                driver=FakeDriver(error=RuntimeError("temporary network failure")),
                account=_account(),
                registry=registry,
                observed_at=NOW + timedelta(days=1),
            )
        )
    assert registry.save_count == save_count
    assert registry.inventory == initial.inventory
    assert registry.inventory is not None
    assert registry.inventory.resumes[0].lifecycle is ResumeLifecycle.ACTIVE


def test_json_registry_persists_deleted_history_for_future_runs(
    workspace_tmp_dir,
) -> None:
    registry = JsonResumeRegistry(workspace_tmp_dir)
    asyncio.run(
        reconcile_account_resumes(
            driver=FakeDriver([_resume("resume-1", "ML")]),
            account=_account(),
            registry=registry,
            observed_at=NOW,
        )
    )
    asyncio.run(
        reconcile_account_resumes(
            driver=FakeDriver([]),
            account=_account(),
            registry=registry,
            observed_at=NOW + timedelta(days=1),
        )
    )
    loaded = asyncio.run(
        registry.load(account_key="account", source_profile="profile")
    )
    assert loaded is not None
    assert loaded.by_source_id["resume-1"].lifecycle is ResumeLifecycle.DELETED
    assert loaded.by_source_id["resume-1"].binding_key == "ml"


def test_published_unpublished_published_preserves_binding_and_apply_selection() -> None:
    registry = MemoryRegistry()
    first = _sync(registry, [_resume("resume-1", "ML")])
    assert first.inventory.by_source_id["resume-1"].selectable_for_auto_apply is True

    unpublished = _sync(
        registry,
        [_resume("resume-1", "ML", status={"id": "not_published"})],
        observed_at=NOW + timedelta(hours=1),
    )
    current = unpublished.inventory.by_source_id["resume-1"]
    assert current.binding_key == "ml"
    assert current.auto_apply is True
    assert current.upstream_status == "not_published"
    assert current.selectable_for_auto_apply is False

    republished = _sync(
        registry,
        [_resume("resume-1", "ML", status={"id": "published"})],
        observed_at=NOW + timedelta(hours=2),
    )
    current = republished.inventory.by_source_id["resume-1"]
    assert current.binding_key == "ml"
    assert current.upstream_status == "published"
    assert current.selectable_for_auto_apply is True
