from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from careerops_storage import S3JsonStore, S3Settings

from .application_audit import HHApplicationAuditService
from .driver import HHApplicantToolCLI


def _build_driver(args: argparse.Namespace) -> HHApplicantToolCLI:
    return HHApplicantToolCLI(
        config_dir=args.config_dir,
        profile=args.profile,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CareerOPS audited HH applications with SeaweedFS/S3"
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("hh-applicant-tool/config"),
    )
    parser.add_argument("--profile", default="careerops-ml")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("s3-smoke")

    apply_p = sub.add_parser("apply")
    apply_p.add_argument("vacancy_id")
    apply_p.add_argument("--resume-id", required=True)
    apply_p.add_argument("--letter-file", type=Path, required=True)
    apply_p.add_argument(
        "--live",
        action="store_true",
        help="Actually submit to HH. Required safety flag.",
    )

    args = parser.parse_args()
    store = S3JsonStore(S3Settings.from_env())

    if args.command == "s3-smoke":
        run_id = uuid4()
        now = datetime.now(UTC)
        ref = store.put_json(
            (
                "smoke/"
                f"date={now.date().isoformat()}/"
                f"run_id={run_id}/ping.json"
            ),
            {
                "event_type": "careerops.s3.smoke",
                "schema_version": 1,
                "run_id": str(run_id),
                "created_at": now.isoformat(),
                "ok": True,
            },
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "uri": ref.uri,
                    "sha256": ref.sha256,
                    "size_bytes": ref.size_bytes,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "apply":
        if not args.live:
            raise SystemExit(
                "Refusing to submit: add --live after checking vacancy/letter."
            )

        message = args.letter_file.read_text(encoding="utf-8").strip()
        if not message:
            raise SystemExit("Letter file is empty.")

        service = HHApplicationAuditService(
            driver=_build_driver(args),
            store=store,
            profile_id=args.profile,
        )

        result = service.apply(
            vacancy_id=args.vacancy_id,
            resume_id=args.resume_id,
            message=message,
        )

        print(
            json.dumps(
                {
                    "run_id": str(result.run_id),
                    "vacancy_id": result.vacancy_id,
                    "status": result.status,
                    "confirmed": result.confirmed,
                    "before_uri": result.before_uri,
                    "request_uri": result.request_uri,
                    "result_uri": result.result_uri,
                    "after_uri": result.after_uri,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
