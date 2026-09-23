from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Struct

from tests.integration.matching_core.test_decision_contract import (
    _evidence,
    _free_loopback_port,
    _generate_python_stubs,
    _matching_core_binary,
    _request,
    _requirement,
    _selection,
    _stop_process,
    _wait_for_channel,
)

pytestmark = pytest.mark.integration_matching_core


@pytest.fixture
def decision_stub(tmp_path: Path) -> Iterator[tuple[ModuleType, Any]]:
    grpc, _control_pb2, control_pb2_grpc = _generate_python_stubs(tmp_path)
    target = f"127.0.0.1:{_free_loopback_port()}"
    env = os.environ.copy()
    env["CAREEROPS_MATCHING_CORE_LISTEN_ADDR"] = target
    process = subprocess.Popen([str(_matching_core_binary())], env=env, text=True)
    channel = grpc.insecure_channel(target)
    try:
        _wait_for_channel(grpc, channel, process)
        yield grpc, control_pb2_grpc.MatchingCoreDecisionStub(channel)
    finally:
        channel.close()
        _stop_process(process)


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
