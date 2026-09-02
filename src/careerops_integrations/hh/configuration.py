"""Versioned non-secret HH account and broad-discovery TOML contracts."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .runtime import RuntimeMode

_ACCOUNT_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_QUERY_KEY = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class HHConfigError(ValueError):
    """Report an invalid or unreadable HH TOML configuration."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class DiscoveryDefaults(_StrictModel):
    """Technical search/fetch limits shared by the broad query catalog."""

    area: int = Field(default=1, ge=1)
    period: int = Field(default=14, ge=1)
    pages: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=100)
    order_by: str = Field(default="publication_time", min_length=1)
    max_queries_per_run: int = Field(default=50, ge=1)
    search_query_delay_seconds: float = Field(default=1.0, ge=0)
    full_fetch_min_delay_seconds: float = Field(default=1.5, ge=0)
    full_fetch_max_delay_seconds: float = Field(default=3.0, ge=0)
    max_unique_vacancies_per_run: int = Field(default=250, ge=1)
    max_full_fetch_per_run: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def validate_delay_range(self) -> DiscoveryDefaults:
        """Require an ordered technical full-fetch delay range."""

        if self.full_fetch_max_delay_seconds < self.full_fetch_min_delay_seconds:
            raise ValueError(
                "full_fetch_max_delay_seconds must be >= full_fetch_min_delay_seconds"
            )
        return self


class DiscoveryQuerySpec(_StrictModel):
    """One stable, independently auditable HH search query."""

    key: str = Field(min_length=1)
    text: str = Field(min_length=1)
    enabled: bool = True
    area: int | None = Field(default=None, ge=1)
    period: int | None = Field(default=None, ge=1)
    pages: int | None = Field(default=None, ge=1)
    per_page: int | None = Field(default=None, ge=1, le=100)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        """Keep query keys safe for S3 path segments."""

        value = value.strip()
        if _QUERY_KEY.fullmatch(value) is None:
            raise ValueError("query key must match [a-z0-9][a-z0-9-]*")
        return value

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        """Reject whitespace-only HH query text."""

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
        """Reject duplicate query keys inside a set."""

        keys = [query.key for query in self.queries]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"duplicate query keys: {duplicates}")
        return self


@dataclass(frozen=True, slots=True)
class DiscoveryQuery:
    """One enabled query resolved with its owning set and technical defaults."""

    query_set_key: str
    spec: DiscoveryQuerySpec


class DiscoveryConfig(_StrictModel):
    """Complete committed broad-discovery catalog."""

    schema_version: Literal[1]
    defaults: DiscoveryDefaults = Field(default_factory=DiscoveryDefaults)
    query_sets: dict[str, DiscoveryQuerySet] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> DiscoveryConfig:
        """Require safe set keys and globally unique stable query keys."""

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
        """Union ordered query-set references and execute each enabled query once."""

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
        """Return query counts for reporting and catalog regression tests."""

        return {
            key: sum(query.enabled for query in query_set.queries)
            for key, query_set in self.query_sets.items()
        }


class HHResumeBindingConfig(_StrictModel):
    """Explicit policy binding for one stable HH resume identity."""

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
        """Normalize and validate account-scoped identifiers."""

        value = value.strip()
        if _ACCOUNT_KEY.fullmatch(value) is None:
            raise ValueError("key must match [a-z0-9][a-z0-9_-]*")
        return value

    @field_validator("source_resume_id")
    @classmethod
    def normalize_source_resume_id(cls, value: str) -> str:
        """Require a non-empty stable HH identity or example placeholder."""

        value = value.strip()
        if not value:
            raise ValueError("source_resume_id must not be empty")
        return value

    @field_validator("query_sets")
    @classmethod
    def validate_query_sets_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject duplicate query-set references within one resume."""

        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("query set keys must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("duplicate query-set reference within resume")
        return normalized


class HHAccountConfig(_StrictModel):
    """One authenticated HH profile with N resume targets."""

    key: str = Field(min_length=1)
    profile: str = Field(min_length=1)
    enabled: bool = True
    observe_runs_per_day: int = Field(default=3, ge=1)
    apply_runs_per_day: int = Field(default=7, ge=1)
    apply_daily_cap: int = Field(default=100, ge=1)
    max_apply_per_run: int = Field(default=15, ge=1)
    bindings: tuple[HHResumeBindingConfig, ...] = Field(min_length=1)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        """Keep account keys stable and safe for plans and S3 sidecars."""

        value = value.strip()
        if _ACCOUNT_KEY.fullmatch(value) is None:
            raise ValueError("account key must match [a-z0-9][a-z0-9_-]*")
        return value

    @field_validator("profile")
    @classmethod
    def normalize_profile(cls, value: str) -> str:
        """Reject an empty upstream profile selector."""

        value = value.strip()
        if not value:
            raise ValueError("profile must not be empty")
        return value

    @model_validator(mode="after")
    def validate_resume_keys(self) -> HHAccountConfig:
        """Require resume keys to be unique within their account."""

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
        if self.apply_runs_per_day * self.max_apply_per_run < self.apply_daily_cap:
            raise ValueError(
                "apply_runs_per_day * max_apply_per_run must be >= apply_daily_cap"
            )
        return self

    @property
    def enabled_bindings(self) -> tuple[HHResumeBindingConfig, ...]:
        """Return policy bindings enabled for this account runtime."""

        return tuple(binding for binding in self.bindings if binding.enabled)

    @property
    def query_set_keys(self) -> tuple[str, ...]:
        """Union enabled-resume query sets while preserving configured order."""

        result: list[str] = []
        seen: set[str] = set()
        for binding in self.enabled_bindings:
            for key in binding.query_sets:
                if key not in seen:
                    seen.add(key)
                    result.append(key)
        return tuple(result)

    def resolve_binding(self, binding_key: str) -> HHResumeBindingConfig:
        """Resolve one enabled explicit binding without title-based guessing."""

        for binding in self.enabled_bindings:
            if binding.key == binding_key:
                return binding
        raise HHConfigError(
            f"enabled resume binding {binding_key!r} not found in account {self.key!r}"
        )


class HHGlobalConfig(_StrictModel):
    """Shared account runtime settings."""

    timezone: str = Field(default="Europe/Moscow", min_length=1)


class HHAccountsConfig(_StrictModel):
    """Versioned N-account/N-resume runtime topology."""

    schema_version: Literal[1]
    runtime_mode: RuntimeMode = RuntimeMode.OBSERVE
    global_settings: HHGlobalConfig = Field(default_factory=HHGlobalConfig, alias="global")
    accounts: tuple[HHAccountConfig, ...] = Field(min_length=1)

    @field_validator("runtime_mode", mode="before")
    @classmethod
    def parse_runtime_mode(cls, value: Any) -> RuntimeMode:
        """Use the single canonical fail-closed mode parser."""

        if value is None:
            return RuntimeMode.OBSERVE
        if not isinstance(value, (str, RuntimeMode)):
            raise ValueError("runtime_mode must be a string")
        return RuntimeMode.parse(value)

    @model_validator(mode="after")
    def validate_account_keys(self) -> HHAccountsConfig:
        """Require one stable account key per upstream profile."""

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
        """Return only accounts participating in planning and dispatch."""

        return tuple(account for account in self.accounts if account.enabled)

    def resolve_account(self, account_key: str) -> HHAccountConfig:
        """Resolve one enabled account without falling back to another profile."""

        for account in self.enabled_accounts:
            if account.key == account_key:
                if not account.enabled_bindings:
                    raise HHConfigError(
                        f"account {account_key!r} has no enabled resume bindings"
                    )
                return account
        raise HHConfigError(f"enabled HH account not found: {account_key!r}")

    def validate_query_sets(self, discovery: DiscoveryConfig) -> None:
        """Reject every unknown query-set reference, including disabled entries."""

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
    """Read a TOML document with a stable domain error."""

    resolved = Path(path)
    try:
        with resolved.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise HHConfigError(f"could not load HH TOML {resolved}: {exc}") from exc
    return payload


def load_discovery_config(path: str | Path) -> DiscoveryConfig:
    """Load and strictly validate the discovery catalog."""

    try:
        return DiscoveryConfig.model_validate(_load_toml(path))
    except ValidationError as exc:
        raise HHConfigError(f"invalid HH discovery config {Path(path)}: {exc}") from exc


def load_accounts_config(
    path: str | Path,
    *,
    discovery: DiscoveryConfig | None = None,
) -> HHAccountsConfig:
    """Load accounts and optionally validate all discovery references."""

    try:
        accounts = HHAccountsConfig.model_validate(_load_toml(path))
    except ValidationError as exc:
        raise HHConfigError(f"invalid HH accounts config {Path(path)}: {exc}") from exc
    if discovery is not None:
        accounts.validate_query_sets(discovery)
    return accounts


def accounts_config_path_from_env() -> Path:
    """Return the runtime account-config pointer."""

    return Path(
        os.getenv(
            "CAREEROPS_HH_ACCOUNTS_CONFIG",
            "/etc/careerops/hh/accounts.toml",
        )
    )


def discovery_config_path_from_env() -> Path:
    """Return the committed discovery-catalog pointer."""

    return Path(
        os.getenv(
            "CAREEROPS_HH_DISCOVERY_CONFIG",
            "config/hh_discovery.toml",
        )
    )
