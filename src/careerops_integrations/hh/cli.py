from __future__ import annotations

import argparse
import json
from pathlib import Path

from .driver import HHApplicantToolCLI
from .raw import LocalRawStore
from .reader import HHUpstreamSQLiteReader
from .sync import HHVacancySync


def _build(args: argparse.Namespace) -> HHVacancySync:
    """Build the local development sync stack from CLI arguments."""

    reader = HHUpstreamSQLiteReader.from_profile(
        config_dir=args.config_dir,
        profile=args.profile,
    )
    driver = HHApplicantToolCLI(config_dir=args.config_dir, profile=args.profile)
    return HHVacancySync(
        reader=reader,
        driver=driver,
        raw_store=LocalRawStore(args.raw_root),
    )


def main() -> None:
    """Inspect, fetch, or locally synchronize HH vacancies."""

    parser = argparse.ArgumentParser(description="CareerOPS HH integration")
    parser.add_argument("--config-dir", type=Path, default=Path("hh-applicant-tool/config"))
    parser.add_argument("--profile", default="careerops-ml")
    parser.add_argument("--raw-root", type=Path, default=Path(".careerops/raw/_lab"))

    sub = parser.add_subparsers(dest="command", required=True)
    p_inspect = sub.add_parser("inspect")
    p_inspect.add_argument("--limit", type=int, default=10)
    p_fetch = sub.add_parser("fetch")
    p_fetch.add_argument("vacancy_id")
    p_sync = sub.add_parser("sync")
    p_sync.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()
    sync = _build(args)

    if args.command == "inspect":
        print(json.dumps(
            list(sync.reader.iter_vacancies(limit=args.limit)),
            ensure_ascii=False, indent=2, default=str,
        ))
    elif args.command == "fetch":
        print(sync.sync_ids([args.vacancy_id])[0].model_dump_json(indent=2))
    else:
        results = sync.sync_recent(limit=args.limit)
        print(json.dumps([
            {
                "id": item.canonical.source_entity_id,
                "title": item.canonical.title,
                "company": item.canonical.company_name,
                "relations": list(item.operational.relations),
                "has_test": item.operational.has_test,
                "raw_uri": item.raw.raw_uri,
            }
            for item in results
        ], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
