"""Authoritative HH resume reconciliation with fail-safe lifecycle semantics."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .configuration import HHAccountConfig, HHResumeBindingConfig


class ResumeLifecycle(StrEnum):
    """Lifecycle of one stable HH source_resume_id in an account inventory."""

    ACTIVE = "active"
    DELETED = "deleted"


class ResumeInventoryError(RuntimeError):
    """Report invalid authoritative inventory or persisted reconciliation state."""


class ResumeSyncDriver(Protocol):
    """Existing HH transport operation required for reconciliation."""

    def list_resumes(self) -> list[dict[str, Any]]:
        """Return an authoritative /resumes/mine item list or raise."""

        ...


class ReconciledResume(BaseModel):
    """Current lifecycle and separate CareerOPS binding for one HH resume."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_profile: str = Field(min_length=1)
    source_resume_id: str = Field(min_length=1)
    current_title: str | None = None
    upstream_status: str | None = None
    lifecycle: ResumeLifecycle
    first_seen_at: datetime
    last_seen_at: datetime
    inactive_at: datetime | None = None
    binding_key: str | None = None
    binding_enabled: bool = False
    target_key: str | None = None
    query_sets: tuple[str, ...] = ()
    auto_apply: bool = False
    binding_version: int | None = Field(default=None, ge=1)
    content_sha256: str = Field(min_length=64, max_length=64)
    source_payload: dict[str, Any]

    @field_validator("first_seen_at", "last_seen_at", "inactive_at")
    @classmethod
    def require_aware(cls, value: datetime | None) -> datetime | None:
        """Normalize lifecycle timestamps to UTC."""

        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("resume lifecycle timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @property
    def assigned(self) -> bool:
        """Return whether an explicit enabled target/policy binding exists."""

        return (
            self.binding_key is not None
            and self.binding_enabled
            and self.target_key is not None
        )

    @property
    def selectable_for_evaluation(self) -> bool:
        """Return whether this resume may participate in new evaluations."""

        return self.lifecycle is ResumeLifecycle.ACTIVE and self.assigned

    @property
    def selectable_for_auto_apply(self) -> bool:
        """Return whether explicit policy permits new automatic applications."""

        return (
            self.selectable_for_evaluation
            and self.auto_apply
            and self.upstream_status == "published"
        )


class AccountResumeInventory(BaseModel):
    """Persisted reconciliation snapshot for one account/profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    account_key: str = Field(min_length=1)
    source_profile: str = Field(min_length=1)
    reconciled_at: datetime
    resumes: tuple[ReconciledResume, ...] = ()

    @field_validator("reconciled_at")
    @classmethod
    def normalize_reconciled_at(cls, value: datetime) -> datetime:
        """Require a timezone-aware reconciliation timestamp."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reconciled_at must be timezone-aware")
        return value.astimezone(UTC)

    @property
    def by_source_id(self) -> dict[str, ReconciledResume]:
        """Index the immutable snapshot by stable HH resume identity."""

        return {resume.source_resume_id: resume for resume in self.resumes}

    @property
    def active_resumes(self) -> tuple[ReconciledResume, ...]:
        """Return every resume currently present in /resumes/mine."""

        return tuple(
            resume
            for resume in self.resumes
            if resume.lifecycle is ResumeLifecycle.ACTIVE
        )

    @property
    def evaluation_resumes(self) -> tuple[ReconciledResume, ...]:
        """Return active resumes with an explicit enabled binding."""

        return tuple(
            resume for resume in self.resumes if resume.selectable_for_evaluation
        )

    @property
    def auto_apply_resumes(self) -> tuple[ReconciledResume, ...]:
        """Return the stricter active, assigned, explicitly auto-apply set."""

        return tuple(
            resume for resume in self.resumes if resume.selectable_for_auto_apply
        )


class ResumeRegistry(Protocol):
    """Authoritative current-state boundary, PostgreSQL-backed in runtime."""

    async def load(
        self,
        *,
        account_key: str,
        source_profile: str,
    ) -> AccountResumeInventory | None:
        """Load the last account inventory, if any."""

        ...

    async def save(self, inventory: AccountResumeInventory) -> None:
        """Atomically persist one authoritative reconciliation result."""

        ...


class JsonResumeRegistry:
    """Explicit dev/bootstrap fallback that preserves deleted resume history."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, account_key: str) -> Path:
        return self.root / f"account={account_key}.json"

    async def load(
        self,
        *,
        account_key: str,
        source_profile: str,
    ) -> AccountResumeInventory | None:
        """Load and identity-check one account snapshot."""

        path = self._path(account_key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            inventory = AccountResumeInventory.model_validate(payload)
        except (OSError, ValueError) as exc:
            raise ResumeInventoryError(
                f"could not load resume registry {path}: {exc}"
            ) from exc
        if inventory.account_key != account_key:
            raise ResumeInventoryError(
                f"resume registry account mismatch in {path}: "
                f"{inventory.account_key!r} != {account_key!r}"
            )
        if inventory.source_profile != source_profile:
            raise ResumeInventoryError(
                f"resume registry profile mismatch in {path}: "
                f"{inventory.source_profile!r} != {source_profile!r}"
            )
        return inventory

    async def save(self, inventory: AccountResumeInventory) -> None:
        """Replace one account state atomically without deleting history entries."""

        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(inventory.account_key)
        temporary = path.with_suffix(path.suffix + ".tmp")
        payload = inventory.model_dump(mode="json")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)


@dataclass(frozen=True, slots=True)
class ResumeReconciliationResult:
    """New snapshot plus explicit lifecycle changes for audit/reporting."""

    inventory: AccountResumeInventory
    registered_ids: tuple[str, ...]
    updated_ids: tuple[str, ...]
    reactivated_ids: tuple[str, ...]
    deleted_ids: tuple[str, ...]

    def audit_payload(self) -> dict[str, Any]:
        """Return CareerOPS-owned metadata without duplicating source resume bodies."""

        return {
            "event_type": "hh.account.resumes.reconciled",
            "schema_version": 1,
            "account_key": self.inventory.account_key,
            "source_profile": self.inventory.source_profile,
            "reconciled_at": self.inventory.reconciled_at.isoformat(),
            "registered_ids": list(self.registered_ids),
            "updated_ids": list(self.updated_ids),
            "reactivated_ids": list(self.reactivated_ids),
            "deleted_ids": list(self.deleted_ids),
            "resumes": [
                {
                    "source_resume_id": resume.source_resume_id,
                    "current_title": resume.current_title,
                    "upstream_status": resume.upstream_status,
                    "lifecycle": resume.lifecycle.value,
                    "content_sha256": resume.content_sha256,
                    "binding_key": resume.binding_key,
                    "binding_enabled": resume.binding_enabled,
                    "target_key": resume.target_key,
                    "query_sets": list(resume.query_sets),
                    "auto_apply": resume.auto_apply,
                    "selectable_for_evaluation": resume.selectable_for_evaluation,
                    "selectable_for_auto_apply": resume.selectable_for_auto_apply,
                    "binding_version": resume.binding_version,
                }
                for resume in self.inventory.resumes
            ],
        }


def resume_state_dir_from_env() -> Path:
    """Return the explicit JSON fallback directory, unused by PostgreSQL runtime."""

    return Path(
        os.getenv(
            "CAREEROPS_HH_RESUME_STATE_DIR",
            ".careerops/hh-resumes",
        )
    )


def _canonical_source_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _title(payload: dict[str, Any]) -> str | None:
    value = payload.get("title")
    if value is None:
        value = payload.get("name")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _upstream_status(payload: dict[str, Any]) -> str | None:
    """Extract HH publication state without treating inventory presence as published."""

    status = payload.get("status")
    if status is None:
        return None
    if isinstance(status, dict):
        status = status.get("id")
    if not isinstance(status, str) or not status.strip():
        raise ResumeInventoryError("HH resume inventory item has invalid status")
    return status.strip()


def _binding_values(
    binding: HHResumeBindingConfig | None,
    previous: ReconciledResume | None,
) -> dict[str, Any]:
    """Resolve explicit binding state without ever matching on title."""

    if binding is not None:
        return {
            "binding_key": binding.key,
            "binding_enabled": binding.enabled,
            "target_key": binding.target_key,
            "query_sets": binding.query_sets,
            "auto_apply": binding.enabled and binding.auto_apply,
            "binding_version": binding.binding_version,
        }
    if previous is not None and previous.binding_key is not None:
        return {
            "binding_key": previous.binding_key,
            "binding_enabled": False,
            "target_key": previous.target_key,
            "query_sets": previous.query_sets,
            "auto_apply": False,
            "binding_version": previous.binding_version,
        }
    return {
        "binding_key": None,
        "binding_enabled": False,
        "target_key": None,
        "query_sets": (),
        "auto_apply": False,
        "binding_version": None,
    }


def _meaningfully_changed(
    previous: ReconciledResume,
    current: ReconciledResume,
) -> bool:
    """Ignore last_seen-only changes when reporting reconciliation updates."""

    fields = (
        "current_title",
        "upstream_status",
        "lifecycle",
        "inactive_at",
        "binding_key",
        "binding_enabled",
        "target_key",
        "query_sets",
        "auto_apply",
        "binding_version",
        "content_sha256",
    )
    return any(getattr(previous, field) != getattr(current, field) for field in fields)


async def reconcile_account_resumes(
    *,
    driver: ResumeSyncDriver,
    account: HHAccountConfig,
    registry: ResumeRegistry,
    observed_at: datetime | None = None,
) -> ResumeReconciliationResult:
    """Reconcile one authoritative account list without destructive failure fallback."""

    now = (observed_at or datetime.now(UTC)).astimezone(UTC)

    # This call and full payload validation happen before loading/saving mutable state.
    # Any transport/auth/schema error therefore cannot turn known resumes into deletions.
    source_items = driver.list_resumes()
    if not isinstance(source_items, list):
        raise ResumeInventoryError("HH list_resumes did not return a list")

    current_items: dict[str, dict[str, Any]] = {}
    for source_item in source_items:
        if not isinstance(source_item, dict):
            raise ResumeInventoryError("HH resume inventory item is not an object")
        source_resume_id = str(source_item.get("id") or "").strip()
        if not source_resume_id:
            raise ResumeInventoryError("HH resume inventory item has no stable id")
        if source_resume_id in current_items:
            raise ResumeInventoryError(
                f"HH resume inventory contains duplicate id {source_resume_id!r}"
            )
        current_items[source_resume_id] = deepcopy(source_item)

    previous_inventory = await registry.load(
        account_key=account.key,
        source_profile=account.profile,
    )
    previous_by_id = (
        previous_inventory.by_source_id if previous_inventory is not None else {}
    )
    bindings_by_source_id = {
        binding.source_resume_id: binding for binding in account.bindings
    }

    registered: list[str] = []
    updated: list[str] = []
    reactivated: list[str] = []
    reconciled: list[ReconciledResume] = []

    for source_resume_id, source_payload in current_items.items():
        previous = previous_by_id.get(source_resume_id)
        binding = bindings_by_source_id.get(source_resume_id)
        current = ReconciledResume(
            source_profile=account.profile,
            source_resume_id=source_resume_id,
            current_title=_title(source_payload),
            upstream_status=_upstream_status(source_payload),
            lifecycle=ResumeLifecycle.ACTIVE,
            first_seen_at=previous.first_seen_at if previous is not None else now,
            last_seen_at=now,
            inactive_at=None,
            content_sha256=_canonical_source_hash(source_payload),
            source_payload=source_payload,
            **_binding_values(binding, previous),
        )
        if previous is None:
            registered.append(source_resume_id)
        else:
            if previous.lifecycle is ResumeLifecycle.DELETED:
                reactivated.append(source_resume_id)
            if _meaningfully_changed(previous, current):
                updated.append(source_resume_id)
        reconciled.append(current)

    deleted: list[str] = []
    for source_resume_id, previous in previous_by_id.items():
        if source_resume_id in current_items:
            continue
        inactive_at = previous.inactive_at or now
        current = previous.model_copy(
            update={
                "lifecycle": ResumeLifecycle.DELETED,
                "inactive_at": inactive_at,
                "binding_enabled": False,
                "auto_apply": False,
            }
        )
        if previous.lifecycle is ResumeLifecycle.ACTIVE:
            deleted.append(source_resume_id)
        reconciled.append(current)

    inventory = AccountResumeInventory(
        account_key=account.key,
        source_profile=account.profile,
        reconciled_at=now,
        resumes=tuple(sorted(reconciled, key=lambda item: item.source_resume_id)),
    )
    await registry.save(inventory)
    return ResumeReconciliationResult(
        inventory=inventory,
        registered_ids=tuple(sorted(registered)),
        updated_ids=tuple(sorted(updated)),
        reactivated_ids=tuple(sorted(reactivated)),
        deleted_ids=tuple(sorted(deleted)),
    )


def source_resume_ids(resumes: Sequence[ReconciledResume]) -> tuple[str, ...]:
    """Return stable IDs for audit metadata and resume+vacancy dedup boundaries."""

    return tuple(resume.source_resume_id for resume in resumes)


def resume_vacancy_dedup_key(
    source_resume_id: str,
    vacancy_id: str,
) -> tuple[str, str]:
    """Keep application duplicate protection scoped to resume plus vacancy."""

    resume_id = source_resume_id.strip()
    normalized_vacancy_id = vacancy_id.strip()
    if not resume_id or not normalized_vacancy_id:
        raise ValueError("resume_id and vacancy_id must not be empty")
    return resume_id, normalized_vacancy_id
