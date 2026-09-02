"""Cross-component regressions reserved for the integration-test task.

Component-local runtime, reconciliation, OBSERVE, PostgreSQL, claim, and
scheduler tests live beside their respective implementation test modules.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

import careerops_integrations.hh.batch_cli as batch_cli
from careerops_integrations.hh.configuration import HHAccountsConfig
from careerops_integrations.hh.resume_sync import (
    AccountResumeInventory,
    ReconciledResume,
    ResumeLifecycle,
    ResumeReconciliationResult,
)
from careerops_integrations.hh.runtime import HHExternalWriteGuard, RuntimeMode

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Ref:
    uri: str


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, Any] = {}

    async def put_json(
        self,
        key: str,
        payload: Any,
        *,
        collected_at: datetime | None = None,
    ) -> Ref:
        assert collected_at is None
        self.objects[key] = deepcopy(payload)
        return Ref(uri=f"s3://careerops-raw/_lab/hh/{key}")


def _accounts() -> HHAccountsConfig:
    return HHAccountsConfig.model_validate(
        {
            "schema_version": 1,
            "runtime_mode": "apply",
            "accounts": [
                {
                    "key": "account",
                    "profile": "upstream-profile",
                    "observe_runs_per_day": 3,
                    "apply_daily_cap": 100,
                    "bindings": [
                        {
                            "key": "ml",
                            "source_resume_id": "resume-1",
                            "target_key": "ml-target",
                            "auto_apply": True,
                            "binding_version": 4,
                            "query_sets": ["ml_core"],
                        },
                        {
                            "key": "backend",
                            "source_resume_id": "resume-2",
                            "target_key": "backend-target",
                            "auto_apply": True,
                            "binding_version": 8,
                            "query_sets": ["python_backend_core"],
                        },
                    ],
                }
            ],
        }
    )


def _reconciliation() -> ResumeReconciliationResult:
    account = _accounts().accounts[0]
    resumes = tuple(
        ReconciledResume(
            source_profile=account.profile,
            source_resume_id=binding.source_resume_id,
            current_title=binding.key,
            upstream_status="published",
            lifecycle=ResumeLifecycle.ACTIVE,
            first_seen_at=NOW,
            last_seen_at=NOW,
            binding_key=binding.key,
            binding_enabled=True,
            target_key=binding.target_key,
            query_sets=binding.query_sets,
            auto_apply=True,
            binding_version=binding.binding_version,
            content_sha256=("a" if binding.key == "ml" else "b") * 64,
            source_payload={"id": binding.source_resume_id, "title": binding.key},
        )
        for binding in account.bindings
    )
    return ResumeReconciliationResult(
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


@pytest.mark.asyncio
async def test_integration_account_apply_without_scheduler_quota_fails_closed() -> None:
    args = batch_cli._parser().parse_args(
        ["--mode", "apply", "--account-key", "account"]
    )
    with pytest.raises(ValueError, match="requires --account-quota-remaining"):
        await batch_cli._run_apply(
            args=args,
            store=FakeStore(),  # type: ignore[arg-type]
            accounts=_accounts(),
            guard=HHExternalWriteGuard(RuntimeMode.APPLY, True),
            registry=object(),  # type: ignore[arg-type]
            claim_store=object(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_integration_account_apply_runs_all_bindings_with_one_scheduler_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = _accounts()
    reconciliation = _reconciliation()
    store = FakeStore()
    args = batch_cli._parser().parse_args(
        [
            "--mode",
            "apply",
            "--account-key",
            "account",
            "--max-responses",
            "10",
            "--account-quota-remaining",
            "3",
        ]
    )

    async def fake_reconcile(**kwargs: Any) -> ResumeReconciliationResult:
        assert kwargs["account"].profile == "upstream-profile"
        assert kwargs["driver"].profile == "upstream-profile"
        return reconciliation

    calls: list[tuple[str, int, tuple[str, ...]]] = []

    async def fake_run_apply_batch(store: Any, options: Any, *, driver: Any):
        calls.append((options.resume_id, options.max_responses, options.query_sets))
        consumed = 2 if options.resume_id == "resume-1" else 1
        return {
            "run_id": f"batch-{options.resume_id}",
            "summary_uri": f"s3://summary/{options.resume_id}",
            "discovered": 5,
            "prefiltered": 1,
            "full_fetched": 4,
            "accepted": consumed,
            "submitted": consumed,
            "confirmed": consumed,
            "external_writes_attempted": consumed,
            "quota_consumed": consumed,
            "failed": 0,
            "stopped_on_captcha": False,
            "reasons": {"accepted": consumed},
        }

    monkeypatch.setattr(batch_cli, "_reconcile", fake_reconcile)
    monkeypatch.setattr(batch_cli, "run_apply_batch", fake_run_apply_batch)

    summary = await batch_cli._run_apply(
        args=args,
        store=store,  # type: ignore[arg-type]
        accounts=accounts,
        guard=HHExternalWriteGuard(RuntimeMode.APPLY, True),
        registry=object(),  # type: ignore[arg-type]
        claim_store=object(),  # type: ignore[arg-type]
    )

    assert calls == [
        ("resume-1", 3, ("ml_core",)),
        ("resume-2", 1, ("python_backend_core",)),
    ]
    assert summary["source_profile"] == "upstream-profile"
    assert summary["eligible_resume_count"] == 2
    assert summary["evaluated_resume_count"] == 2
    assert summary["submitted"] == 3
    assert summary["external_writes_attempted"] == 3
    assert summary["quota_consumed"] == 3
    assert summary["account_quota_remaining_after_run"] == 0
    assert [run["source_resume_id"] for run in summary["resume_runs"]] == [
        "resume-1",
        "resume-2",
    ]
    run_payload = next(
        payload
        for key, payload in store.objects.items()
        if key.startswith("account-runs/") and key.endswith("/run.json")
    )
    assert [item["binding_version"] for item in run_payload["selected_bindings"]] == [
        4,
        8,
    ]
