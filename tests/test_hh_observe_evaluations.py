from __future__ import annotations

from uuid import UUID

import pytest
from support.hh_observe import (
    NOW,
    FakeObserveDriver,
    FakeObserveStore,
    MemoryQueryCursorStore,
    make_discovery,
    make_pages,
    no_sleep,
)

from careerops_integrations.hh.configuration import HHAccountConfig
from careerops_integrations.hh.observe import HHObserveRunner
from careerops_integrations.hh.resume_sync import (
    AccountResumeInventory,
    ReconciledResume,
    ResumeLifecycle,
    ResumeReconciliationResult,
)
from careerops_integrations.hh.runtime import HHExternalWriteGuard, RuntimeMode


@pytest.mark.asyncio
async def test_observe_persists_independent_vacancy_resume_evaluation_pairs() -> None:
    account = HHAccountConfig.model_validate(
        {
            "key": "junior",
            "profile": "profile-junior",
            "bindings": [
                {
                    "key": "ml",
                    "source_resume_id": "resume-ml",
                    "target_key": "ml-target",
                    "auto_apply": False,
                    "query_sets": ["ml_core"],
                },
                {
                    "key": "backend",
                    "source_resume_id": "resume-backend",
                    "target_key": "backend-target",
                    "auto_apply": False,
                    "query_sets": ["python_backend_core"],
                },
            ],
        }
    )
    resumes = tuple(
        ReconciledResume(
            source_profile=account.profile,
            source_resume_id=binding.source_resume_id,
            current_title=binding.key,
            lifecycle=ResumeLifecycle.ACTIVE,
            first_seen_at=NOW,
            last_seen_at=NOW,
            binding_key=binding.key,
            binding_enabled=True,
            target_key=binding.target_key,
            query_sets=binding.query_sets,
            auto_apply=False,
            binding_version=binding.binding_version,
            content_sha256=("a" if binding.key == "ml" else "b") * 64,
            source_payload={"id": binding.source_resume_id, "title": binding.key},
        )
        for binding in account.bindings
    )
    reconciliation = ResumeReconciliationResult(
        inventory=AccountResumeInventory(
            account_key=account.key,
            source_profile=account.profile,
            reconciled_at=NOW,
            resumes=resumes,
        ),
        registered_ids=tuple(resume.source_resume_id for resume in resumes),
        updated_ids=(),
        reactivated_ids=(),
        deleted_ids=(),
    )
    store = FakeObserveStore()
    result = await HHObserveRunner(
        driver=FakeObserveDriver(make_pages()),
        store=store,
        account=account,
        discovery=make_discovery(),
        resume_reconciliation=reconciliation,
        query_cursor_store=MemoryQueryCursorStore(),
        external_write_guard=HHExternalWriteGuard(RuntimeMode.OBSERVE, False),
        sleep=no_sleep,
        clock=lambda: NOW,
    ).run(run_id=UUID("55555555-5555-4555-8555-555555555555"))

    sidecar = store.objects[
        f"{result.run_prefix}/candidates/vacancy_id=1/evaluation_candidates.json"
    ]
    evaluations = sidecar["evaluations"]
    assert [item["source_resume_id"] for item in evaluations] == [
        "resume-ml",
        "resume-backend",
    ]
    assert {
        (
            item["duplicate_key"]["account_key"],
            item["duplicate_key"]["source_profile"],
            item["duplicate_key"]["source_resume_id"],
            item["duplicate_key"]["vacancy_id"],
        )
        for item in evaluations
    } == {
        ("junior", "profile-junior", "resume-ml", "1"),
        ("junior", "profile-junior", "resume-backend", "1"),
    }
    assert result.summary["evaluation_candidate_count"] == 6

    ml_only_sidecar = store.objects[
        f"{result.run_prefix}/candidates/vacancy_id=2/evaluation_candidates.json"
    ]
    backend_evaluation = next(
        item
        for item in ml_only_sidecar["evaluations"]
        if item["source_resume_id"] == "resume-backend"
    )
    assert backend_evaluation["matched_query_keys"] == ["ml-query"]
    assert backend_evaluation["matched_query_sets"] == ["ml_core"]
    assert backend_evaluation["resume_query_sets"] == ["python_backend_core"]
    assert backend_evaluation["provenance_overlap"] == {
        "has_overlap": False,
        "matched_query_keys": [],
        "matched_query_sets": [],
    }
