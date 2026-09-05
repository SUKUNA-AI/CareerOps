"""Bounded command-line entry point for the CareerOPS HH v2 source worker."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .service import default_accounts_config_path, default_worker_id, run_account_worker


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Execute already-persisted CareerOPS v2 HH source tasks for one account. "
            "This command is read-only toward HH and does not schedule search generations."
        )
    )
    parser.add_argument("--account-key", required=True)
    parser.add_argument(
        "--accounts-config",
        type=Path,
        default=default_accounts_config_path(),
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("hh-applicant-tool/config"),
        help="hh-applicant-tool configuration directory",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=25,
        help="Maximum persistent source tasks to execute before exiting",
    )
    parser.add_argument(
        "--lease-seconds",
        type=int,
        default=300,
        help="Lease duration for one claimed source task",
    )
    parser.add_argument(
        "--worker-id",
        default=default_worker_id(),
        help="Diagnostic lease owner id; defaults to hostname:pid",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    summary = await run_account_worker(
        account_key=args.account_key,
        accounts_config=args.accounts_config,
        config_dir=args.config_dir,
        max_tasks=args.max_tasks,
        lease_seconds=args.lease_seconds,
        worker_id=args.worker_id,
    )
    print(json.dumps(summary.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    """Run one bounded worker invocation and emit a single JSON summary."""

    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
