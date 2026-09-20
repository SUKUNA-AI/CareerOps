from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Struct

from tests.integration.matching_core.test_decision_contract import (
    _evidence,
    _request,
    _requirement,
    _selection,
    decision_stub,
)

pytestmark = pytest.mark.integration_matching_core


@pytest.mark.parametrize(
    "invalid_date",
    [
        "2026-02-31",
        "2026-04-31",
        "2025-02-29",
        "0000-01-01",
        "2026-1x-01",
    ],
)
def test_direct_grpc_rejects_invalid_calendar_date(
    decision_stub: tuple[ModuleType, Any],
    invalid_date: str,
) -> None:
    grpc, stub = decision_stub
    threshold = {"metric": "experience_years", "minimum": "1", "maximum": None}
    request = _request(
        requirements=[_requirement("req-python", "Python", threshold=threshold)],
        evidence=[
            _evidence(
                "ev-python",
                "Python",
                time_span={
                    "start_date": invalid_date,
                    "end_date": "2026-09-01",
                    "currently_active": False,
                },
            )
        ],
        selections=[_selection("req-python", ["ev-python"])],
    )

    with pytest.raises(grpc.RpcError) as error:
        stub.Evaluate(ParseDict(request, Struct()), timeout=2)

    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert error.value.details() == "invalid date"
