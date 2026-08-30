from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from careerops_integrations.hh.application_audit import (
    HHApplicationAuditService,
    HHApplicationBlocked,
)


@dataclass
class _Ref:
    uri: str


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, Any] = {}

    def put_json(self, key: str, payload: Any) -> _Ref:
        self.objects[key] = payload
        return _Ref(uri=f"s3://careerops-raw/_lab/hh/{key}")


class FakeDriver:
    def __init__(self, before: dict[str, Any]) -> None:
        self.before = before
        self.submitted = False

    def fetch_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        payload = dict(self.before)
        if self.submitted:
            payload["relations"] = ["got_response"]
        return payload

    def submit_application(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> dict[str, Any]:
        self.submitted = True
        return {}


def _vacancy(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": "123",
        "name": "ML Engineer",
        "relations": [],
        "archived": False,
        "closed_for_applicants": False,
        "has_test": False,
        "response_url": None,
        "employer": {"name": "Example"},
    }
    payload.update(overrides)
    return payload


def test_audited_application_persists_four_objects() -> None:
    store = FakeStore()
    service = HHApplicationAuditService(
        driver=FakeDriver(_vacancy()),
        store=store,
        profile_id="careerops-ml",
    )

    result = service.apply(
        vacancy_id="123",
        resume_id="resume",
        message="hello",
    )

    assert result.confirmed is True
    assert result.status == "submitted"

    names = {key.rsplit("/", 1)[-1] for key in store.objects}
    assert names == {
        "vacancy_before.json",
        "application_request.json",
        "vacancy_after.json",
        "application_result.json",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"relations": ["got_response"]},
        {"archived": True},
        {"closed_for_applicants": True},
        {"has_test": True},
        {"response_url": "https://example.com/apply"},
    ],
)
def test_hard_guards_block_application(overrides: dict[str, Any]) -> None:
    service = HHApplicationAuditService(
        driver=FakeDriver(_vacancy(**overrides)),
        store=FakeStore(),
        profile_id="careerops-ml",
    )

    with pytest.raises(HHApplicationBlocked):
        service.apply(
            vacancy_id="123",
            resume_id="resume",
            message="hello",
        )
