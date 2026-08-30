from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from careerops_contracts import RawVacancyRef


def canonical_json_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class LocalRawStore:
    """Development RAW sink; SeaweedFS replaces it later."""

    def __init__(self, root: str | Path = ".careerops/raw/_lab") -> None:
        self.root = Path(root).resolve()

    def write(
        self,
        *,
        payload: dict,
        run_id: UUID,
        vacancy_id: str,
        collected_at: datetime | None = None,
    ) -> RawVacancyRef:
        collected_at = collected_at or datetime.now(UTC)
        data = canonical_json_bytes(payload)
        digest = hashlib.sha256(data).hexdigest()

        directory = (
            self.root / "hh"
            / f"ingest_date={collected_at.date().isoformat()}"
            / f"run_id={run_id}"
        )
        directory.mkdir(parents=True, exist_ok=True)

        path = directory / f"vacancy_{vacancy_id}_{digest[:12]}.json"
        if not path.exists():
            path.write_bytes(data)

        return RawVacancyRef(
            source="hh",
            source_entity_id=str(vacancy_id),
            raw_uri=path.as_uri(),
            content_hash=digest,
            collected_at=collected_at,
        )
