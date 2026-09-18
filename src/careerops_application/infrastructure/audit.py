from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from careerops_storage.s3 import S3JsonStore, S3Settings


class S3ApplicationAuditStore:
    def __init__(self, settings: S3Settings) -> None:
        self._settings = settings

    async def write_event(
        self,
        *,
        application_id: UUID,
        event: str,
        payload: Mapping[str, object],
    ) -> str:
        now = datetime.now(UTC)
        key = (
            f"applications/{application_id}/{now:%Y/%m/%d}/"
            f"{now:%H%M%S.%fZ}-{event}-{uuid4().hex}.json"
        )
        body: dict[str, object] = {
            "application_id": str(application_id),
            "event": event,
            "recorded_at": now.isoformat(),
            "payload": dict(payload),
        }
        async with S3JsonStore(self._settings) as store:
            ref = await store.put_json(key, body, collected_at=now)
        return ref.uri
