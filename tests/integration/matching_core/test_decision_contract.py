from __future__ import annotations

import importlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Struct

pytestmark = pytest.mark.integration_matching_core

DECISION_PROTOCOL_VERSION = "careerops.matching-core.decision.v1"
_SHA = "a" * 64


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _matching_core_binary() -> Path:
    configured = os.environ.get("CAREEROPS_MATCHING_CORE_BINARY", "").strip()
    if not configured:
        pytest.fail("CAREEROPS_MATCHING_CORE_BINARY is required for integration_matching_core")
    binary = Path(configured).expanduser().resolve()
    if not binary.is_file():
        pytest.fail(f"matching-core binary does not exist: {binary}")
    return binary


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _generate_python_stubs(tmp_path: Path) -> tuple[ModuleType, ModuleType, ModuleType]:
    grpc = importlib.import_module("grpc")
    grpc_tools_protoc = importlib.import_module("grpc_tools.protoc")
    proto_root = _repo_root() / "proto"
    proto_file = proto_root / "careerops/matching_core/v1/control.proto"
    result = grpc_tools_protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{proto_root}",
            f"--python_out={tmp_path}",
            f"--grpc_python_out={tmp_path}",
            str(proto_file),
        ]
    )
    assert result == 0
    sys.path.insert(0, str(tmp_path))
    try:
        importlib.invalidate_caches()
        control_pb2 = importlib.import_module("careerops.matching_core.v1.control_pb2")
        control_pb2_grpc = importlib.import_module("careerops.matching_core.v1.control_pb2_grpc")
    finally:
        sys.path.remove(str(tmp_path))
    return grpc, control_pb2, control_pb2_grpc


def _wait_for_channel(grpc: ModuleType, channel: object, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"matching-core exited before ready: returncode={process.returncode}")
        try:
            grpc.channel_ready_future(channel).result(timeout=0.2)
            return
        except grpc.FutureTimeoutError:
            continue
    pytest.fail("matching-core gRPC endpoint did not become ready within 5 seconds")


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


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


def _subject(name: str) -> dict[str, Any]:
    return {"text": name, "normalized": name.casefold(), "dictionary_key": None}


def _requirement(
    requirement_id: str,
    subject: str,
    *,
    importance: str = "mandatory",
    modality: str = "required",
    polarity: str = "positive",
) -> dict[str, Any]:
    return {
        "requirement_id": requirement_id,
        "kind": "technology",
        "statement": subject,
        "subjects": [_subject(subject)],
        "activity": None,
        "context": "qualification",
        "importance": importance,
        "modality": modality,
        "polarity": polarity,
        "threshold": None,
        "source_refs": [{"source_path": "test", "rendered_value": subject}],
    }


def _evidence(
    evidence_id: str,
    subject: str,
    *,
    actor: str = "self",
    polarity: str = "positive",
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "kind": "experience",
        "statement": subject,
        "subjects": [_subject(subject)],
        "activity": None,
        "actor_scope": actor,
        "context": "commercial",
        "polarity": polarity,
        "strength": "direct",
        "time_span": None,
        "source_refs": [{"source_path": "test", "rendered_value": subject}],
    }


def _selection(
    requirement_id: str,
    evidence_ids: list[str],
    *,
    state: str = "ranked",
    selected_ids: list[str] | None = None,
) -> dict[str, Any]:
    if state != "ranked":
        return {
            "requirement_id": requirement_id,
            "state": state,
            "query_text": None,
            "pool_evidence_ids": [],
            "pool_render_sha256": None,
            "candidates": [],
        }
    selected = evidence_ids if selected_ids is None else selected_ids
    return {
        "requirement_id": requirement_id,
        "state": "ranked",
        "query_text": requirement_id,
        "pool_evidence_ids": evidence_ids,
        "pool_render_sha256": _SHA,
        "candidates": [
            {"evidence_id": item, "rank": index, "relevance_score": 0.01}
            for index, item in enumerate(selected, start=1)
        ],
    }


def _request(
    *,
    requirements: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    selections: list[dict[str, Any]],
    groups: list[dict[str, Any]] | None = None,
    calibration_version: str = "cal-v1",
) -> dict[str, Any]:
    if groups is None:
        groups = [
            {
                "group_id": "root",
                "operator": "all",
                "requirement_ids": [item["requirement_id"] for item in requirements],
                "child_group_ids": [],
                "condition": None,
            }
        ]
    scoring = {
        "calibration_version": "cal-v1",
        "component_weights": {"mandatory_coverage": "1"},
        "candidate_min_score": "50",
        "mandatory_min_support": "1",
        "candidate_ttl_seconds": 3600,
    }
    return {
        "protocol_version": DECISION_PROTOCOL_VERSION,
        "input_fingerprint": _SHA,
        "requirement_set_sha256": _SHA,
        "resume_evidence_set_sha256": "b" * 64,
        "evidence_candidate_set_sha256": "c" * 64,
        "qualification_version": "qualification-v1",
        "scoring_version": "scoring-v1",
        "calibration_version": calibration_version,
        "policy_version": "policy-v1",
        "as_of": "2026-09-16",
        "requirement_set": {
            "schema_version": "careerops.processing.requirement-set.v2",
            "source_key": "hh",
            "source_entity_id": "vac-1",
            "semantic_content_hash": _SHA,
            "normalized_schema_version": "vacancy-v1",
            "normalization_version": "norm-v1",
            "dictionary_version": "dict-v1",
            "extraction_version": "requirements-v2",
            "requirements": requirements,
            "groups": groups,
            "root_group_id": "root",
        },
        "resume_evidence_set": {
            "schema_version": "careerops.processing.resume-evidence-set.v2",
            "source_key": "hh",
            "account_key": "account-1",
            "source_entity_id": "resume-1",
            "semantic_content_hash": "d" * 64,
            "normalized_schema_version": "resume-v1",
            "normalization_version": "norm-v1",
            "dictionary_version": "dict-v1",
            "evidence_version": "evidence-v2",
            "evidence": evidence,
        },
        "evidence_candidate_set": {
            "schema_version": "careerops.processing.evidence-candidate-set.v1",
            "input_fingerprint": _SHA,
            "requirement_set_sha256": _SHA,
            "resume_evidence_set_sha256": "b" * 64,
            "jina": {
                "model_id": "jinaai/jina-reranker-v3.5",
                "model_revision": "test",
                "model_code_revision": "test",
                "tokenizer_revision": "test",
                "runtime_backend": "test",
                "dtype_or_quantization": "float32",
                "torch_version": "test",
                "transformers_version": "test",
                "top_k": 5,
                "token_budget": 4096,
                "block_protocol": "single-list-v1",
            },
            "selections": selections,
        },
        "target_policy_content": {"filtering": {}, "scoring": scoring},
        "vacancy": None,
    }


def _call(stub: Any, payload: dict[str, Any]) -> dict[str, Any]:
    message = ParseDict(payload, Struct())
    response = stub.Evaluate(message, timeout=2)
    return MessageToDict(response, preserving_proto_field_name=True)


def test_native_direct_match_and_low_jina_score(decision_stub: tuple[ModuleType, Any]) -> None:
    _grpc, stub = decision_stub
    request = _request(
        requirements=[_requirement("req-python", "Python")],
        evidence=[_evidence("ev-python", "Python")],
        selections=[_selection("req-python", ["ev-python"])],
    )

    result = _call(stub, request)

    evaluation = result["qualification_set"]["evaluations"][0]
    assert evaluation["state"] == "matched"
    assert evaluation["support"] == {"lower": "1", "upper": "1"}
    assert result["decision"]["decision"] == "application_candidate"
    assert result["decision"]["reason_codes"] == ["match.policy_satisfied"]


def test_native_uncertainty_and_critical_contradiction(
    decision_stub: tuple[ModuleType, Any],
) -> None:
    _grpc, stub = decision_stub
    uncertain = _request(
        requirements=[_requirement("req-k8s", "Kubernetes")],
        evidence=[_evidence("ev-k8s", "Kubernetes", actor="team")],
        selections=[_selection("req-k8s", ["ev-k8s"])],
    )
    hard_skip = _request(
        requirements=[
            _requirement(
                "req-windows",
                "Windows",
                modality="prohibited",
                polarity="negative",
            )
        ],
        evidence=[_evidence("ev-windows", "Windows")],
        selections=[_selection("req-windows", ["ev-windows"])],
        calibration_version="calibration-unset",
    )

    uncertain_result = _call(stub, uncertain)
    hard_skip_result = _call(stub, hard_skip)

    assert uncertain_result["qualification_set"]["evaluations"][0]["state"] == "unknown"
    assert uncertain_result["decision"]["decision"] == "review"
    assert hard_skip_result["qualification_set"]["evaluations"][0]["state"] == "contradicted"
    assert hard_skip_result["decision"]["decision"] == "skip"
    assert hard_skip_result["decision"]["reason_codes"] == [
        "requirements.critical_contradiction"
    ]


def test_native_not_required_is_neutral_inside_any(
    decision_stub: tuple[ModuleType, Any],
) -> None:
    _grpc, stub = decision_stub
    requirements = [
        _requirement(
            "req-legacy",
            "Legacy",
            importance="optional",
            modality="not_required",
        ),
        _requirement("req-python", "Python"),
    ]
    groups = [
        {
            "group_id": "root",
            "operator": "all",
            "requirement_ids": [],
            "child_group_ids": ["language"],
            "condition": None,
        },
        {
            "group_id": "language",
            "operator": "any",
            "requirement_ids": ["req-legacy", "req-python"],
            "child_group_ids": [],
            "condition": None,
        },
    ]
    request = _request(
        requirements=requirements,
        evidence=[],
        selections=[
            _selection("req-legacy", [], state="skipped_not_required"),
            _selection("req-python", [], state="no_evidence"),
        ],
        groups=groups,
    )

    result = _call(stub, request)

    language = next(
        item for item in result["qualification_set"]["groups"] if item["group_id"] == "language"
    )
    assert result["qualification_set"]["ignored_requirement_ids"] == ["req-legacy"]
    assert language["support"] == {"lower": "0", "upper": "1"}


def test_native_batch_rpc_evaluates_multiple_pairs(
    decision_stub: tuple[ModuleType, Any],
) -> None:
    _grpc, stub = decision_stub
    matched = _request(
        requirements=[_requirement("req-python", "Python")],
        evidence=[_evidence("ev-python", "Python")],
        selections=[_selection("req-python", ["ev-python"])],
    )
    uncertain = _request(
        requirements=[_requirement("req-k8s", "Kubernetes")],
        evidence=[_evidence("ev-k8s", "Kubernetes", actor="team")],
        selections=[_selection("req-k8s", ["ev-k8s"])],
    )
    payload = ParseDict(
        {"protocol_version": DECISION_PROTOCOL_VERSION, "items": [matched, uncertain]},
        Struct(),
    )

    response = stub.EvaluateBatch(payload, timeout=2)
    result = MessageToDict(response, preserving_proto_field_name=True)

    assert [item["decision"]["decision"] for item in result["items"]] == [
        "application_candidate",
        "review",
    ]
