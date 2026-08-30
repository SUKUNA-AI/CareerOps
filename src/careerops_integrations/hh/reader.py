from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any


class HHUpstreamSQLiteReader:
    """Read-only index reader for hh-applicant-tool SQLite state."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()
        if not self.database_path.is_file():
            raise FileNotFoundError(
                f"hh-applicant-tool SQLite database not found: {self.database_path}"
            )

    @classmethod
    def from_profile(cls, *, config_dir: str | Path, profile: str) -> "HHUpstreamSQLiteReader":
        return cls(Path(config_dir) / profile / "data")

    def _connect(self) -> sqlite3.Connection:
        uri = f"{self.database_path.as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def columns(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("PRAGMA table_info(vacancies)").fetchall()
        return {str(row["name"]) for row in rows}

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM vacancies").fetchone()
        return int(row["n"])

    def iter_vacancies(self, *, limit: int | None = None) -> Iterator[dict[str, Any]]:
        columns = self.columns()
        preferred = [
            "id", "name", "alternate_url", "area_id", "area_name",
            "salary_from", "salary_to", "currency", "gross", "remote",
            "experience", "professional_roles", "published_at",
            "created_at", "updated_at",
        ]
        selected = [column for column in preferred if column in columns]
        if "id" not in selected or "name" not in selected:
            raise RuntimeError("Unexpected upstream vacancies schema: id/name missing")

        query = f"SELECT {', '.join(selected)} FROM vacancies ORDER BY id DESC"
        params: tuple[int, ...] = ()
        if limit is not None:
            if limit < 1:
                return
            query += " LIMIT ?"
            params = (limit,)

        with self._connect() as conn:
            for row in conn.execute(query, params):
                yield dict(row)

    def vacancy_ids(self, *, limit: int | None = None) -> list[str]:
        return [str(row["id"]) for row in self.iter_vacancies(limit=limit)]
