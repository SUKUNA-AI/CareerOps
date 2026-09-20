from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import threading
from collections.abc import Sequence

from ..infrastructure.audit import S3ApplicationAuditStore
from ..infrastructure.hh_applicant_transport import HHApplicantTransport
from ..infrastructure.postgres import PostgresApplicationUnitOfWork
from ..owner import ApplicationOwner
from .config import ApplicationServiceSettings
from .health import HealthServer

logger = logging.getLogger(__name__)


async def _run(settings: ApplicationServiceSettings) -> None:
    stop = asyncio.Event()
    ready = threading.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    audit = S3ApplicationAuditStore(settings.s3)
    owner = ApplicationOwner(
        uow_factory=lambda: PostgresApplicationUnitOfWork(
            settings.postgres_dsn,
            account_limit_cooldown_seconds=settings.account_limit_cooldown_seconds,
        ),
        transport=HHApplicantTransport(
            config_dir=settings.hh_config_dir,
            request_timeout_seconds=settings.transport_timeout_seconds,
        ),
        audit=audit,
        worker_id=settings.worker_id,
        lease_seconds=settings.lease_seconds,
        retry_after_seconds=settings.retry_after_seconds,
        reconcile_after_seconds=settings.reconcile_after_seconds,
        operation_timeout_seconds=settings.operation_timeout_seconds,
        cover_letter=settings.cover_letter,
    )
    health = HealthServer(settings.health_host, settings.health_port, ready_event=ready)
    health.start()
    reconciliation_burst = 0
    dependencies_ready = False
    try:
        while not stop.is_set():
            did_work = False
            try:
                recovered = await owner.recover_expired_leases()
                if recovered:
                    logger.warning("recovered %d expired application leases", recovered)
                if not dependencies_ready:
                    await audit.check_ready()
                    dependencies_ready = True
                    ready.set()
                    logger.info("application owner dependencies are ready")

                if reconciliation_burst >= settings.max_reconciliation_burst:
                    did_work = await owner.execute_next()
                    if did_work:
                        reconciliation_burst = 0
                    else:
                        did_work = await owner.reconcile_next()
                        if did_work:
                            reconciliation_burst = 1
                else:
                    did_work = await owner.reconcile_next()
                    if did_work:
                        reconciliation_burst += 1
                    else:
                        did_work = await owner.execute_next()
                        if did_work:
                            reconciliation_burst = 0
            except Exception:
                if dependencies_ready:
                    logger.warning("application owner dependency state became unhealthy")
                dependencies_ready = False
                ready.clear()
                logger.exception("application owner iteration failed")
            if not did_work:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.poll_interval_seconds)
                except TimeoutError:
                    pass
    finally:
        ready.clear()
        health.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="careerops-application-owner")
    parser.add_argument("command", nargs="?", default="serve", choices=("serve",))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    del args
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run(ApplicationServiceSettings.from_env()))
    return 0
