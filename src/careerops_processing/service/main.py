"""Executable shell отдельного контейнера careerops-processing"""

from __future__ import annotations

import argparse
import json
import signal
import threading

from .config import ProcessingRuntimeConfig
from .health import HealthServer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="careerops-processing")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check-config", help="validate runtime wiring and exit")
    subparsers.add_parser("serve", help="run the Processing service shell")
    return parser


def _serve(config: ProcessingRuntimeConfig) -> int:
    stop = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        del signum, frame
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    health = HealthServer(config.health_host, config.health_port)
    health.start()
    try:
        stop.wait()
    finally:
        health.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = ProcessingRuntimeConfig.from_env()

    if args.command == "check-config":
        print(json.dumps(config.safe_summary(), indent=2, sort_keys=True))
        return 0
    if args.command == "serve":
        return _serve(config)

    raise AssertionError(f"unhandled command: {args.command}")
