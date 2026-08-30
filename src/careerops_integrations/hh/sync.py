from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID, uuid4

from .driver import HHApplicantToolCLI
from .mapper import extract_operational, map_hh_vacancy
from .models import SyncedHHVacancy
from .raw import LocalRawStore
from .reader import HHUpstreamSQLiteReader


class HHVacancySync:
    def __init__(self, *, reader, driver, raw_store) -> None:
        self.reader: HHUpstreamSQLiteReader = reader
        self.driver: HHApplicantToolCLI = driver
        self.raw_store: LocalRawStore = raw_store

    def sync_ids(
        self,
        vacancy_ids: Iterable[str],
        *,
        run_id: UUID | None = None,
    ) -> list[SyncedHHVacancy]:
        run_id = run_id or uuid4()
        result: list[SyncedHHVacancy] = []

        for vacancy_id in vacancy_ids:
            payload = self.driver.fetch_vacancy(vacancy_id)
            raw = self.raw_store.write(
                payload=payload,
                run_id=run_id,
                vacancy_id=str(vacancy_id),
            )
            result.append(
                SyncedHHVacancy(
                    raw=raw,
                    canonical=map_hh_vacancy(payload, raw=raw),
                    operational=extract_operational(payload),
                    upstream_payload=payload,
                )
            )
        return result

    def sync_recent(self, *, limit: int = 10) -> list[SyncedHHVacancy]:
        return self.sync_ids(self.reader.vacancy_ids(limit=limit))
