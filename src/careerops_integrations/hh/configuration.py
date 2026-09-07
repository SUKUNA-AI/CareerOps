"""Versioned non-secret HH source topology and discovery TOML contracts."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_ACCOUNT_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_QUERY_KEY = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class HHConfigError(ValueError):
    """Report an invalid or unreadable HH TOML configuration."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class DiscoveryDefaults(_StrictModel):
    """Source request defaults shared by the broad query catalog."""

    area: int = Field(default=1, ge=1)
    period: int = Field(default=14, ge=1)
    per_page: int = Field(default=50, ge=1, le=100)
    order_by: str = Field(default="publication_time", min_length=1)


class DiscoveryQuerySpec(_StrictModel):
    """One stable, independently auditable HH search query."""

    key: str = Field(min_length=1)
    text: str = Field(min_length=1)
    enabled: bool = True
    area: int | None = Field(default=None, ge=1)
    period: int | None = Field(default=None, ge=1)
    per_page: int | None = Field(default=None, ge=1, le=100)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        value = value.strip()
        if _QUERY_KEY.fullmatch(value) is None:
            raise ValueError("query key must match [a-z0-9][a-z0-9-]*")
        return value

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query text must not be empty")
        return value


class DiscoveryQuerySet(_StrictModel):
    """Versioned ordered query set."""

    version: int = Field(default=1, ge=1)
    queries: tuple[DiscoveryQuerySpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_local_keys(self) -> DiscoveryQuerySet:
        keys = [query.key for query in self.queries]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"duplicate query keys: {duplicates}")
        return self


@dataclass(frozen=True, slots=True)
class DiscoveryQuery:
    """One enabled query resolved with its owning set and source parameters."""

    query_set_key: str
    spec: DiscoveryQuerySpec


class DiscoveryConfig(_StrictModel):
    """Complete committed broad-discovery catalog."""

    schema_version: Literal[1]
    defaults: DiscoveryDefaults = Field(default_factory=DiscoveryDefaults)
    query_sets: dict[str, DiscoveryQuerySet] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> DiscoveryConfig:
        seen: dict[str, str] = {}
        for set_key, query_set in self.query_sets.items():
            if _ACCOUNT_KEY.fullmatch(set_key) is None:
                raise ValueError(
                    f"query set key {set_key!r} must match [a-z0-9][a-z0-9_-]*"
                )
            for query in query_set.queries:
                previous = seen.get(query.key)
                if previous is not None:
                    raise ValueError(
                        f"duplicate global query key {query.key!r} in "
                        f"{previous!r} and {set_key!r}"
                    )
                seen[query.key] = set_key
        return self

    def select_queries(
        self,
        query_set_keys: list[str] | tuple[str, ...],
    ) -> tuple[DiscoveryQuery, ...]:
        selected: list[DiscoveryQuery] = []
        seen_sets: set[str] = set()
        for set_key in query_set_keys:
            if set_key in seen_sets:
                continue
            seen_sets.add(set_key)
            query_set = self.query_sets.get(set_key)
            if query_set is None:
                raise HHConfigError(f"unknown discovery query set: {set_key!r}")
            selected.extend(
                DiscoveryQuery(query_set_key=set_key, spec=query)
                for query in query_set.queries
                if query.enabled
            )
        return tuple(selected)

    @property
    def enabled_query_count_by_set(self) -> dict[str, int]:
        return {
            key: sum(query.enabled for query in query_set.queries)
            for key, query_set in self.query_sets.items()
        }


class HHResumeBindingConfig(_StrictModel):
    """Explicit binding from one stable HH resume identity to one target."""

    key: str = Field(min_length=1)
    source_resume_id: str = Field(min_length=1)
    target_key: str = Field(min_length=1)
    enabled: bool = True
    auto_apply: bool = False
    binding_version: int = Field(default=1, ge=1)
    query_sets: tuple[str, ...] = Field(min_length=1)

    @field_validator("key", "target_key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        value = value.strip()
        if _ACCOUNT_KEY.fullmatch(value) is None:
            raise ValueError("key must match [a-z0-9][a-z0-9_-]*")
        return value

    @field_validator("source_resume_id")
    @classmethod
    def normalize_source_resume_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source_resume_id must not be empty")
        return value

    @field_validator("query_sets")
    @classmethod
    def validate_query_sets_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("query set keys must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("duplicate query-set reference within resume")
        return normalized


class HHAccountConfig(_StrictModel):
    """One authenticated HH source profile with explicit resume bindings."""

    key: str = Field(min_length=1)
    profile: str = Field(min_length=1)
    enabled: bool = True
    bindings: tuple[HHResumeBindingConfig, ...] = Field(min_length=1)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        value = value.strip()
        if _ACCOUNT_KEY.fullmatch(value) is None:
            raise ValueError("account key must match [a-z0-9][a-z0-9_-]*")
        return value

    @field_validator("profile")
    @classmethod
    def normalize_profile(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("profile must not be empty")
        return value

    @model_validator(mode="after")
    def validate_resume_keys(self) -> HHAccountConfig:
        keys = [binding.key for binding in self.bindings]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(
                f"duplicate resume keys in account {self.key!r}: {duplicates}"
            )
        source_ids = [binding.source_resume_id for binding in self.bindings]
        duplicate_source_ids = sorted(
            {source_id for source_id in source_ids if source_ids.count(source_id) > 1}
        )
        if duplicate_source_ids:
            raise ValueError(
                f"duplicate source_resume_id values in account {self.key!r}: "
                f"{duplicate_source_ids}"
            )
        return self

    @property
    def enabled_bindings(self) -> tuple[HHResumeBindingConfig, ...]:
        return tuple(binding for binding in self.bindings if binding.enabled)

    @property
    def query_set_keys(self) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        for binding in self.enabled_bindings:
            for key in binding.query_sets:
                if key not in seen:
                    seen.add(key)
                    result.append(key)
        return tuple(result)

    def resolve_binding(self, binding_key: str) -> HHResumeBindingConfig:
        for binding in self.enabled_bindings:
            if binding.key == binding_key:
                return binding
        raise HHConfigError(
            f"enabled resume binding {binding_key!r} not found in account {self.key!r}"
        )


class HHAccountsConfig(_StrictModel):
    """Versioned N-account/N-resume source topology."""

    schema_version: Literal[1]
    accounts: tuple[HHAccountConfig, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_account_keys(self) -> HHAccountsConfig:
        keys = [account.key for account in self.accounts]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"duplicate account keys: {duplicates}")
        profiles = [account.profile for account in self.accounts]
        duplicate_profiles = sorted(
            {profile for profile in profiles if profiles.count(profile) > 1}
        )
        if duplicate_profiles:
            raise ValueError(f"duplicate account profiles: {duplicate_profiles}")
        return self

    @property
    def enabled_accounts(self) -> tuple[HHAccountConfig, ...]:
        return tuple(account for account in self.accounts if account.enabled)

    def resolve_account(self, account_key: str) -> HHAccountConfig:
        for account in self.enabled_accounts:
            if account.key == account_key:
                if not account.enabled_bindings:
                    raise HHConfigError(
                        f"account {account_key!r} has no enabled resume bindings"
                    )
                return account
        raise HHConfigError(f"enabled HH account not found: {account_key!r}")

    def validate_query_sets(self, discovery: DiscoveryConfig) -> None:
        known = set(discovery.query_sets)
        unknown: list[str] = []
        for account in self.accounts:
            for binding in account.bindings:
                for key in binding.query_sets:
                    if key not in known:
                        unknown.append(
                            f"account={account.key}, binding={binding.key}, query_set={key}"
                        )
        if unknown:
            raise HHConfigError("unknown discovery query sets: " + "; ".join(unknown))


def _load_toml(path: str | Path) -> dict[str, Any]:
    resolved = Path(path)
    try:
        with resolved.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise HHConfigError(f"could not load HH TOML {resolved}: {exc}") from exc
    return payload


def load_discovery_config(path: str | Path) -> DiscoveryConfig:
    try:
        return DiscoveryConfig.model_validate(_load_toml(path))
    except ValidationError as exc:
        raise HHConfigError(f"invalid HH discovery config {Path(path)}: {exc}") from exc


def load_accounts_config(
    path: str | Path,
    *,
    discovery: DiscoveryConfig | None = None,
) -> HHAccountsConfig:
    try:
        accounts = HHAccountsConfig.model_validate(_load_toml(path))
    except ValidationError as exc:
        raise HHConfigError(f"invalid HH accounts config {Path(path)}: {exc}") from exc
    if discovery is not None:
        accounts.validate_query_sets(discovery)
    return accounts


def accounts_config_path_from_env() -> Path:
    return Path(
        os.getenv(
            "CAREEROPS_HH_ACCOUNTS_CONFIG",
            "/etc/careerops/hh/accounts.toml",
        )
    )


def discovery_config_path_from_env() -> Path:
    return Path(
        os.getenv(
            "CAREEROPS_HH_DISCOVERY_CONFIG",
            "config/hh_discovery.toml",
        )
    )
