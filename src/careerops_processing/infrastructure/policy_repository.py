"""Файловый репозиторий версионированных TargetPolicy для Processing v2"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from careerops_processing.contracts import FilterPolicy, TargetPolicy


class TargetPolicyRepository(Protocol):
    """Контракт получения текущей или конкретной версии TargetPolicy"""

    def get_current(self, target_key: str) -> TargetPolicy: ...

    def get(self, target_key: str, policy_version: str) -> TargetPolicy: ...


class _PolicyIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    current_policy_version: str = Field(min_length=1)
    path: str = Field(min_length=1)


class _PolicyIndex(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: int = Field(ge=1, le=1)
    targets: dict[str, _PolicyIndexEntry]


class _PolicyFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    target_key: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    content: dict[str, Any]


class FileTargetPolicyRepository:
    """Читает immutable policy-файлы и проверяет их связь с current index"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._index = self._load_index()

    def _load_index(self) -> _PolicyIndex:
        path = self.root / "index.json"
        try:
            payload = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"не удалось прочитать policy index: {path}") from exc
        return _PolicyIndex.model_validate_json(payload)

    def _resolve_policy_path(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("policy path выходит за пределы policy repository") from exc
        return path

    def _load_document(self, relative_path: str) -> _PolicyFile:
        path = self._resolve_policy_path(relative_path)
        try:
            payload = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"не удалось прочитать policy file: {path}") from exc
        return _PolicyFile.model_validate_json(payload)

    def _materialize(self, document: _PolicyFile) -> TargetPolicy:
        policy = TargetPolicy.from_content(
            target_key=document.target_key,
            schema_version=document.schema_version,
            policy_version=document.policy_version,
            content=document.content,
        )
        FilterPolicy.from_target_policy(policy)
        return policy

    def get_current(self, target_key: str) -> TargetPolicy:
        try:
            entry = self._index.targets[target_key]
        except KeyError as exc:
            raise KeyError(f"неизвестный target_key: {target_key}") from exc

        document = self._load_document(entry.path)
        if document.target_key != target_key:
            raise ValueError("target_key в index и policy file не совпадают")
        if document.policy_version != entry.current_policy_version:
            raise ValueError("current_policy_version в index и policy file не совпадает")
        return self._materialize(document)

    def get(self, target_key: str, policy_version: str) -> TargetPolicy:
        current = self.get_current(target_key)
        if current.policy_version == policy_version:
            return current

        version_path = self.root / target_key / f"{policy_version}.json"
        document = self._load_document(str(version_path.relative_to(self.root)))
        if document.target_key != target_key:
            raise ValueError("target_key в запрошенном policy file не совпадает")
        if document.policy_version != policy_version:
            raise ValueError("policy_version в запрошенном policy file не совпадает")
        return self._materialize(document)

    def load_all_current(self) -> dict[str, TargetPolicy]:
        return {
            target_key: self.get_current(target_key)
            for target_key in sorted(self._index.targets)
        }
