from __future__ import annotations

import importlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.integration_matching_core

SERVICE_NAME = "careerops-matching-core"
SERVICE_VERSION = "0.1.0"
CONTROL_PROTOCOL_VERSION = "careerops.matching-core.control.v1"
SERVICE_FULL_NAME = "careerops.matching_core.v1.MatchingCoreControl"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _matching_core_binary() -> Path:
    configured = os.environ.get("CAREEROPS_MATCHING_CORE_BINARY", "").strip()
    if not configured:
        pytest.fail(
            "CAREEROPS_MATCHING_CORE_BINARY is required for integration_matching_core"
        )
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
    assert proto_file.is_file()

    result = grpc_tools_protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{proto_root}",
            f"--python_out={tmp_path}",
            f"--grpc_python_out={tmp_path}",
            str(proto_file),
        ]
    )
    assert result == 0, "Python gRPC stub generation from canonical control.proto failed"

    sys.path.insert(0, str(tmp_path))
    try:
        importlib.invalidate_caches()
        control_pb2 = importlib.import_module("careerops.matching_core.v1.control_pb2")
        control_pb2_grpc = importlib.import_module(
            "careerops.matching_core.v1.control_pb2_grpc"
        )
    finally:
        sys.path.remove(str(tmp_path))

    return grpc, control_pb2, control_pb2_grpc


def _wait_for_channel(grpc: ModuleType, channel: object, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            pytest.fail(f"matching-core exited before gRPC became ready: returncode={returncode}")
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


def test_python_and_cpp_share_the_canonical_control_contract(tmp_path: Path) -> None:
    grpc, control_pb2, control_pb2_grpc = _generate_python_stubs(tmp_path)

    service = control_pb2.DESCRIPTOR.services_by_name["MatchingCoreControl"]
    assert service.full_name == SERVICE_FULL_NAME
    assert [method.name for method in service.methods] == ["Health", "GetCapabilities"]

    binary = _matching_core_binary()
    target = f"127.0.0.1:{_free_loopback_port()}"
    env = os.environ.copy()
    env["CAREEROPS_MATCHING_CORE_LISTEN_ADDR"] = target

    process = subprocess.Popen([str(binary)], env=env, text=True)
    channel = grpc.insecure_channel(target)
    try:
        _wait_for_channel(grpc, channel, process)
        stub = control_pb2_grpc.MatchingCoreControlStub(channel)

        health = stub.Health(control_pb2.HealthRequest(), timeout=2)
        assert health.status == control_pb2.HealthResponse.SERVING
        assert health.service_name == SERVICE_NAME
        assert health.service_version == SERVICE_VERSION
        assert health.protocol_version == CONTROL_PROTOCOL_VERSION

        capabilities = stub.GetCapabilities(control_pb2.CapabilitiesRequest(), timeout=2)
        assert capabilities.service_name == SERVICE_NAME
        assert capabilities.service_version == SERVICE_VERSION
        assert list(capabilities.supported_control_protocol_versions) == [
            CONTROL_PROTOCOL_VERSION
        ]
        assert capabilities.evaluate_match_available is False
        assert capabilities.evaluate_batch_available is False

        healthcheck = subprocess.run(
            [str(binary), "--healthcheck"],
            env=env,
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        assert healthcheck.returncode == 0, (
            "compiled matching-core --healthcheck failed against its running gRPC service\n"
            f"stdout:\n{healthcheck.stdout}\n"
            f"stderr:\n{healthcheck.stderr}"
        )
    finally:
        channel.close()
        _stop_process(process)
