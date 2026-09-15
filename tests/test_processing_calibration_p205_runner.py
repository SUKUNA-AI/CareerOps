from __future__ import annotations

import pytest

from careerops_processing.calibration.p205_runner import (
    P205RequirementAlignment,
    P205RuntimeCase,
    run_p205_cases,
)
from careerops_processing.contracts import JinaVersionBundle
from careerops_processing.selector import RerankerResponse, RerankerScore
from tests.support.processing import evidence, evidence_set, jina, requirement, requirement_set


class _FakeRerankerClient:
    def __init__(self) -> None:
        self.document_pools: list[tuple[str, ...]] = []

    async def rerank(
        self,
        *,
        query: str,
        documents: tuple[str, ...],
        top_n: int,
        token_budget: int,
        expected_version: JinaVersionBundle,
    ) -> RerankerResponse:
        del query, token_budget, expected_version
        self.document_pools.append(documents)
        ranked = (
            RerankerScore(index=2, relevance_score=0.9),
            RerankerScore(index=0, relevance_score=0.8),
            RerankerScore(index=1, relevance_score=0.7),
        )
        return RerankerResponse(results=ranked[:top_n], total_tokens=128)


@pytest.mark.asyncio
async def test_p205_runner_uses_full_evidence_pool_and_emits_standard_predictions() -> None:
    requirements = requirement_set(
        requirement("req-python", "Python"),
        requirement("req-sql", "SQL"),
    )
    resume_evidence = evidence_set(
        evidence("e3", "Airflow"),
        evidence("e1", "Python"),
        evidence("e2", "SQL"),
    )
    case = P205RuntimeCase(
        pair_id="pair-1",
        requirement_set=requirements,
        resume_evidence_set=resume_evidence,
        alignments=(
            P205RequirementAlignment(
                gold_requirement_index=0,
                requirement_id="req-python",
            ),
            P205RequirementAlignment(
                gold_requirement_index=1,
                requirement_id="req-sql",
            ),
        ),
    )
    client = _FakeRerankerClient()

    predictions = await run_p205_cases(
        (case,),
        client=client,
        jina=jina(top_k=3),
    )

    assert len(client.document_pools) == 2
    assert all(len(pool) == 3 for pool in client.document_pools)
    assert [item.gold_requirement_index for item in predictions] == [0, 1]
    assert all(item.ranked_evidence_refs == ("e3", "e1", "e2") for item in predictions)


def test_p205_runtime_case_rejects_unknown_requirement_alignment() -> None:
    requirements = requirement_set(requirement("req-python", "Python"))
    resume_evidence = evidence_set(evidence("e1", "Python"))

    with pytest.raises(ValueError, match="unknown requirement ids"):
        P205RuntimeCase(
            pair_id="pair-1",
            requirement_set=requirements,
            resume_evidence_set=resume_evidence,
            alignments=(
                P205RequirementAlignment(
                    gold_requirement_index=0,
                    requirement_id="missing-requirement",
                ),
            ),
        )
