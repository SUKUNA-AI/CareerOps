"""Lossless reconciliation desired Processing work в durable jobs"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .queue import RECONCILIATION_WITHDRAWN, ProcessingPairKey, ProcessingWorkSpec


class ProcessingReconciliationStore(Protocol):
    """Минимальные durable возможности для desired-state reconciliation"""

    async def reconcile_current(self, spec: ProcessingWorkSpec) -> UUID: ...

    async def withdraw_pair(
        self,
        pair: ProcessingPairKey,
        *,
        reason: str = RECONCILIATION_WITHDRAWN,
    ) -> int: ...


@dataclass(frozen=True, slots=True)
class ProcessingReconciliationResult:
    """Наблюдаемый результат одного idempotent reconciliation pass"""

    desired_pairs: int
    withdrawn_pairs: int
    withdrawn_jobs: int
    job_ids: tuple[UUID, ...]


class ProcessingReconciler:
    """Синхронизирует explicit desired work и explicit pair withdrawals"""

    def __init__(self, store: ProcessingReconciliationStore) -> None:
        self._store = store

    async def reconcile(
        self,
        specs: Iterable[ProcessingWorkSpec],
        *,
        withdrawn_pairs: Iterable[ProcessingPairKey] = (),
        withdrawal_reason: str = RECONCILIATION_WITHDRAWN,
    ) -> ProcessingReconciliationResult:
        desired: dict[ProcessingPairKey, ProcessingWorkSpec] = {}
        for spec in specs:
            previous = desired.get(spec.pair)
            if previous is not None and previous != spec:
                raise ValueError(
                    "reconciliation contains conflicting work for "
                    f"vacancy_id={spec.vacancy_id}, binding_id={spec.binding_id}"
                )
            desired[spec.pair] = spec

        withdrawn = set(withdrawn_pairs)
        overlap = withdrawn.intersection(desired)
        if overlap:
            pair = min(overlap)
            raise ValueError(
                "pair cannot be desired and withdrawn in the same reconciliation pass: "
                f"vacancy_id={pair.vacancy_id}, binding_id={pair.binding_id}"
            )

        normalized_reason = withdrawal_reason.strip()
        if withdrawn and not normalized_reason:
            raise ValueError("withdrawal_reason must not be empty when withdrawing pairs")

        job_ids: list[UUID] = []
        for pair in sorted(desired):
            job_ids.append(await self._store.reconcile_current(desired[pair]))

        withdrawn_jobs = 0
        for pair in sorted(withdrawn):
            withdrawn_jobs += await self._store.withdraw_pair(
                pair,
                reason=normalized_reason,
            )

        return ProcessingReconciliationResult(
            desired_pairs=len(desired),
            withdrawn_pairs=len(withdrawn),
            withdrawn_jobs=withdrawn_jobs,
            job_ids=tuple(job_ids),
        )
