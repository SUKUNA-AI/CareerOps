"""Bounded command-line entry point for CareerOPS HH v2 source ingestion."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from careerops_integrations.hh.configuration import discovery_config_path_from_env

from .producer import SourceSeedKind, seed_source_generation
from .service import default_accounts_config_path, default_worker_id, run_account_worker


def _add_accounts_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--accounts-config",
        type=Path,
        default=default_accounts_config_path(),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Persist or execute CareerOPS v2 HH source tasks. Commands are read-only "
            "toward HH and never submit applications."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    seed = commands.add_parser(
        "seed",
        help="Atomically persist root source tasks for one explicit generation",
    )
    seed.add_argument("--account-key", required=True)
    seed.add_argument(
        "--generation-id",
        required=True,
        type=UUID,
        help="Stable orchestration UUID; reuse it when retrying the same generation",
    )
    seed.add_argument(
        "--kind",
        required=True,
        choices=tuple(kind.value for kind in SourceSeedKind),
        help="Persist search roots, resume-sync root, or both",
    )
    _add_accounts_config(seed)
    seed.add_argument(
        "--discovery-config",
        type=Path,
        default=discovery_config_path_from_env(),
        help="Discovery catalog; read only for search/all generation kinds",
    )

    work = commands.add_parser(
        "work",
        help="Execute already-persisted source tasks for one account",
    )
    work.add_argument("--account-key", required=True)
    _add_accounts_config(work)
    work.add_argument(
        "--config-dir",
        type=Path,
        default=Path("hh-applicant-tool/config"),
        help="hh-applicant-tool configuration directory",
    )
    work.add_argument(
        "--max-tasks",
        type=int,
        default=25,
        help="Maximum persistent source tasks to execute before exiting",
    )
    work.add_argument(
        "--lease-seconds",
        type=int,
        default=300,
        help="Lease duration for one claimed source task",
    )
    work.add_argument(
        "--worker-id",
        default=default_worker_id(),
        help="Diagnostic lease owner id; defaults to hostname:pid",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    if args.command == "seed":
        summary = await seed_source_generation(
            account_key=args.account_key,
            generation_id=args.generation_id,
            kind=SourceSeedKind(args.kind),
            accounts_config=args.accounts_config,
            discovery_config=args.discovery_config,
        )
    elif args.command == "work":
        summary = await run_account_worker(
            account_key=args.account_key,
            accounts_config=args.accounts_config,
            config_dir=args.config_dir,
            max_tasks=args.max_tasks,
            lease_seconds=args.lease_seconds,
            worker_id=args.worker_id,
        )
    else:  # pragma: no cover - argparse enforces a known required subcommand.
        raise RuntimeError(f"unsupported HH adapter command: {args.command!r}")

    print(json.dumps(summary.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    """Run one explicit seeding or bounded worker operation and emit JSON."""

    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
