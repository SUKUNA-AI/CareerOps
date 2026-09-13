"""Persistent publication watermarks для lossless HH search pagination

Watermark является advisory stop state, а не source truth
Он двигается только после того, как terminal search-page task уже сохранила RAW object
и child work
Если обновление watermark падает, следующая generation просто перечитывает больше source pages
Control state не должен обгонять durable ingestion и заставлять систему пропускать work
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


class HHSearchWatermarkStore:
    """PostgreSQL v2 owner publication watermarks account/profile/query"""

    def __init__(self, conn: AsyncConnection[Any]) -> None:
        if not conn.autocommit:
            raise ValueError("HHSearchWatermarkStore requires an autocommit connection")
        self._conn = conn

    async def load_for_profile(
        self,
        *,
        account_id: int,
        profile_key: str,
    ) -> dict[str, datetime]:
        """Возвращает последний safely committed publication watermark каждого query"""

        if account_id <= 0:
            raise ValueError("account_id must be positive")
        normalized_profile = profile_key.strip()
        if not normalized_profile:
            raise ValueError("profile_key must not be empty")

        cursor = await self._conn.execute(
            """
            SELECT sw.query_key, sw.published_at
            FROM careerops_v2.source_watermarks AS sw
            JOIN careerops_v2.profiles AS p
              ON p.id = sw.profile_id
             AND p.account_id = sw.account_id
             AND p.source_id = sw.source_id
            JOIN careerops_v2.sources AS s ON s.id = sw.source_id
            WHERE sw.account_id = %s
              AND p.profile_key = %s
              AND s.source_key = 'hh'
            """,
            (account_id, normalized_profile),
        )
        rows = await cursor.fetchall()
        result: dict[str, datetime] = {}
        for query_key, published_at in rows:
            if not isinstance(query_key, str) or not query_key.strip():
                raise TypeError("PostgreSQL returned an invalid HH watermark query key")
            if not isinstance(published_at, datetime):
                raise TypeError("PostgreSQL returned a non-datetime HH watermark")
            result[query_key] = _aware_utc(published_at, "published_at")
        return result

    async def advance(
        self,
        *,
        account_id: int,
        profile_key: str,
        query_key: str,
        published_at: datetime,
        generation_id: UUID,
        observed_at: datetime,
    ) -> None:
        """Монотонно двигает query watermark после durable успеха page"""

        if account_id <= 0:
            raise ValueError("account_id must be positive")
        normalized_profile = profile_key.strip()
        normalized_query = query_key.strip()
        if not normalized_profile:
            raise ValueError("profile_key must not be empty")
        if not normalized_query:
            raise ValueError("query_key must not be empty")
        if generation_id.int == 0:
            raise ValueError("generation_id must not be the nil UUID")

        published = _aware_utc(published_at, "published_at")
        observed = _aware_utc(observed_at, "observed_at")
        cursor = await self._conn.execute(
            """
            INSERT INTO careerops_v2.source_watermarks AS sw (
                source_id,
                account_id,
                profile_id,
                query_key,
                published_at,
                generation_id,
                observed_at
            )
            SELECT p.source_id, p.account_id, p.id, %s, %s, %s, %s
            FROM careerops_v2.profiles AS p
            JOIN careerops_v2.sources AS s ON s.id = p.source_id
            WHERE p.account_id = %s
              AND p.profile_key = %s
              AND s.source_key = 'hh'
            ON CONFLICT (account_id, profile_id, query_key)
            DO UPDATE SET
                published_at = GREATEST(sw.published_at, EXCLUDED.published_at),
                generation_id = CASE
                    WHEN EXCLUDED.published_at >= sw.published_at
                    THEN EXCLUDED.generation_id
                    ELSE sw.generation_id
                END,
                observed_at = GREATEST(sw.observed_at, EXCLUDED.observed_at),
                updated_at = now()
            RETURNING sw.query_key
            """,
            (
                normalized_query,
                published,
                generation_id,
                observed,
                account_id,
                normalized_profile,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(
                "cannot advance HH watermark for an unregistered account/profile: "
                f"account_id={account_id}, profile_key={normalized_profile!r}"
            )
