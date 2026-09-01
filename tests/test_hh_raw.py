from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from careerops_integrations.hh.batch_cli import _write_json
from careerops_integrations.hh.raw import LocalRawStore


@dataclass(frozen=True)
class _Ref:
    uri: str


class FakeStore:
    def __init__(self) -> None:
        self.payload: Any = None
        self.collected_at: datetime | None = None

    async def put_json(
        self,
        key: str,
        payload: Any,
        *,
        collected_at: datetime | None = None,
    ) -> _Ref:
        self.payload = payload
        self.collected_at = collected_at
        return _Ref(uri=f"s3://careerops-raw/_lab/hh/{key}")


def test_local_raw_store(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[bytes] = []

    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, "exists", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda path, data: written.append(data) or len(data),
    )
    ref = LocalRawStore(Path.cwd() / "local-raw-test").write(
        payload={"id": "1", "name": "ML Engineer"},
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        vacancy_id="1",
    )

    assert ref.source == "hh"
    assert len(ref.content_hash) == 64
    assert ref.raw_uri.startswith("file:")
    assert len(written) == 1


@pytest.mark.asyncio
async def test_s3_raw_writer_keeps_source_body_pure() -> None:
    source: dict[str, object] = {"id": "1", "name": "ML Engineer"}
    observed_at = datetime(2026, 8, 30, 11, 30, tzinfo=UTC)
    store = FakeStore()

    await _write_json(
        store,  # type: ignore[arg-type]
        "batches/run.json",
        source,
        collected_at=observed_at,
    )

    assert store.payload == source
    assert "collected_at" not in store.payload
    assert store.collected_at == observed_at
