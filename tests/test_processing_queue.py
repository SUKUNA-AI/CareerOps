from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from careerops_processing.queue import (
    RECONCILIATION_WITHDRAWN,
    ProcessingPairKey,
    ProcessingWorkSpec,
)
from careerops_processing.reconciliation import ProcessingReconciler


def _spec(
    *,
    vacancy_id: int = 1,
    binding_id: int = 2,
    char: str = "a",
) -> ProcessingWorkSpec:
    return ProcessingWorkSpec(
        vacancy_id=vacancy_id,
        binding_id=binding_id,
        binding_version=1,
        input_fingerprint=char * 64,
        input_manifest_uri=f"s3://careerops-artifacts/manifests/{char * 64}.json",
        pipeline_version="processing-v2-test",
        policy_version="policy-v1",
    )


def test_processing_work_spec_rejects_non_s3_manifest() -> None:
    with pytest.raises(ValueError, match="s3://"):
        ProcessingWorkSpec(
            vacancy_id=1,
            binding_id=2,
            binding_version=1,
            input_fingerprint="a" * 64,
            input_manifest_uri="file:///tmp/manifest.json",
            pipeline_version="processing-v2-test",
            policy_version="policy-v1",
        )


def test_processing_work_spec_rejects_invalid_fingerprint() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        ProcessingWorkSpec(
            vacancy_id=1,
            binding_id=2,
            binding_version=1,
            input_fingerprint="NOT-A-HASH",
            input_manifest_uri="s3://careerops-artifacts/manifest.json",
            pipeline_version="processing-v2-test",
            policy_version="policy-v1",
        )


def test_pair_and_work_ids_are_strict_positive_integers() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        ProcessingPairKey(vacancy_id=True, binding_id=2)
    with pytest.raises(ValueError, match="positive integer"):
        _spec(vacancy_id=0)


class _FakeStore:
    def __init__(self) -> None:
        self.seen: list[ProcessingWorkSpec] = []
        self.withdrawn: list[tuple[ProcessingPairKey, str]] = []

    async def reconcile_current(self, spec: ProcessingWorkSpec) -> UUID:
        self.seen.append(spec)
        return uuid4()

    async def withdraw_pair(
        self,
        pair: ProcessingPairKey,
        *,
        reason: str = RECONCILIATION_WITHDRAWN,
    ) -> int:
        self.withdrawn.append((pair, reason))
        return 1


@pytest.mark.asyncio
async def test_reconciler_deduplicates_identical_pair_specs() -> None:
    store = _FakeStore()
    reconciler = ProcessingReconciler(store)
    spec = _spec()

    result = await reconciler.reconcile([spec, spec])

    assert result.desired_pairs == 1
    assert result.withdrawn_pairs == 0
    assert result.withdrawn_jobs == 0
    assert len(result.job_ids) == 1
    assert store.seen == [spec]


@pytest.mark.asyncio
async def test_reconciler_rejects_conflicting_current_work_for_same_pair() -> None:
    store = _FakeStore()
    reconciler = ProcessingReconciler(store)

    with pytest.raises(ValueError, match="conflicting work"):
        await reconciler.reconcile([_spec(char="a"), _spec(char="b")])

    assert store.seen == []


@pytest.mark.asyncio
async def test_reconciler_withdraws_explicit_absent_pairs() -> None:
    store = _FakeStore()
    reconciler = ProcessingReconciler(store)
    pair = ProcessingPairKey(vacancy_id=10, binding_id=20)

    result = await reconciler.reconcile([], withdrawn_pairs=[pair, pair])

    assert result.desired_pairs == 0
    assert result.withdrawn_pairs == 1
    assert result.withdrawn_jobs == 1
    assert store.withdrawn == [(pair, RECONCILIATION_WITHDRAWN)]


@pytest.mark.asyncio
async def test_reconciler_rejects_pair_that_is_both_desired_and_withdrawn() -> None:
    store = _FakeStore()
    reconciler = ProcessingReconciler(store)
    spec = _spec()

    with pytest.raises(ValueError, match="desired and withdrawn"):
        await reconciler.reconcile([spec], withdrawn_pairs=[spec.pair])

    assert store.seen == []
    assert store.withdrawn == []
