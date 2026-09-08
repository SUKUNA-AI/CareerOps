"""Общие PostgreSQL-зависимости HH source adapter"""

from __future__ import annotations

import os
from typing import Any

from psycopg import AsyncConnection


class HHPostgresSettings:
    """Настройки подключения HH source adapter к PostgreSQL v2"""

    def __init__(self, dsn: str) -> None:
        normalized = dsn.strip()
        if not normalized:
            raise ValueError("dsn must not be empty")
        self.dsn = normalized

    @classmethod
    def from_env(cls) -> HHPostgresSettings:
        value = os.getenv("CAREEROPS_V2_POSTGRES_DSN", "").strip()
        if not value:
            raise RuntimeError(
                "CAREEROPS_V2_POSTGRES_DSN is required by the HH v2 source adapter"
            )
        return cls(value)


async def resolve_hh_account_id(
    conn: AsyncConnection[Any],
    *,
    account_key: str,
    profile_key: str,
) -> int:
    """Возвращает внутренний id зарегистрированной пары account/profile для HH"""

    cursor = await conn.execute(
        """
        SELECT a.id
        FROM careerops_v2.accounts AS a
        JOIN careerops_v2.sources AS s ON s.id = a.source_id
        JOIN careerops_v2.profiles AS p
          ON p.account_id = a.id AND p.source_id = a.source_id
        WHERE s.source_key = 'hh'
          AND a.account_key = %s
          AND p.profile_key = %s
        """,
        (account_key, profile_key),
    )
    rows = await cursor.fetchall()
    if len(rows) != 1:
        raise RuntimeError(
            "expected exactly one v2 HH account/profile mapping for "
            f"account={account_key!r}, profile={profile_key!r}; found {len(rows)}"
        )
    return int(rows[0][0])
