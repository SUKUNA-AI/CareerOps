"""Точка запуска отдельного сервиса careerops-processing"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import threading
from contextlib import AsyncExitStack
from datetime import timedelta

from psycopg import AsyncConnection

from careerops_processing.executor import P204Executor, P205Executor, P206Executor, P207Executor
from careerops_processing.infrastructure import (
    HttpJinaRerankerClient,
    PostgresMatchPublicationStore,
    PostgresProcessingJobStore,
    PostgresSemanticArtifactRegistry,
    ProcessingArtifactLoader,
    ProcessingArtifactPublisher,
    ProcessingArtifactStore,
    ProcessingArtifactStoreSettings,
    S3ProcessingInputLoader,
)
from careerops_processing.selector import EvidenceCandidateSelector
from careerops_processing.semantic_cache import P204SemanticArtifactResolver
from careerops_processing.worker import ProcessingWorker, ProcessingWorkerPolicy
from careerops_storage.s3 import S3JsonStore, S3Settings

from .config import ProcessingRuntimeConfig
from .health import HealthServer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="careerops-processing")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check-config", help="проверить настройки подключения и завершиться")
    subparsers.add_parser("serve", help="запустить сервис Processing worker")
    return parser


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, request_stop)
        except NotImplementedError:
            signal.signal(
                signum,
                lambda _signum, _frame: loop.call_soon_threadsafe(request_stop),
            )


async def _serve_async(config: ProcessingRuntimeConfig) -> int:
    stop = asyncio.Event()
    ready = threading.Event()
    _install_signal_handlers(stop)

    health = HealthServer(
        config.health_host,
        config.health_port,
        ready_event=ready,
    )
    health.start()

    manifest_settings = S3Settings(
        endpoint_url=config.s3_endpoint_url,
        access_key=config.s3_access_key,
        secret_key=config.s3_secret_key,
        bucket=config.artifacts_bucket,
        region=config.s3_region,
        prefix=config.artifacts_prefix,
    )
    normalized_settings = S3Settings(
        endpoint_url=config.s3_endpoint_url,
        access_key=config.s3_access_key,
        secret_key=config.s3_secret_key,
        bucket=config.normalized_bucket,
        region=config.s3_region,
        prefix="",
    )
    artifact_settings = ProcessingArtifactStoreSettings(
        endpoint_url=config.s3_endpoint_url,
        access_key=config.s3_access_key,
        secret_key=config.s3_secret_key,
        bucket=config.artifacts_bucket,
        region=config.s3_region,
        prefix=config.artifacts_prefix,
    )

    try:
        async with AsyncExitStack() as stack:
            queue_conn = await stack.enter_async_context(
                await AsyncConnection.connect(config.postgres_dsn, autocommit=True)
            )
            semantic_conn = await stack.enter_async_context(
                await AsyncConnection.connect(config.postgres_dsn, autocommit=True)
            )
            publication_conn = await stack.enter_async_context(
                await AsyncConnection.connect(config.postgres_dsn, autocommit=True)
            )
            manifest_store = await stack.enter_async_context(S3JsonStore(manifest_settings))
            normalized_store = await stack.enter_async_context(S3JsonStore(normalized_settings))
            artifact_store = await stack.enter_async_context(
                ProcessingArtifactStore(artifact_settings)
            )
            reranker_client = await stack.enter_async_context(
                HttpJinaRerankerClient(
                    config.reranker_endpoint_url,
                    timeout_seconds=config.reranker_timeout_seconds,
                )
            )

            publisher = ProcessingArtifactPublisher(artifact_store)
            artifact_loader = ProcessingArtifactLoader(artifact_store)
            loader = S3ProcessingInputLoader(
                manifest_store=manifest_store,
                normalized_store=normalized_store,
            )
            semantic_registry = PostgresSemanticArtifactRegistry(semantic_conn)
            semantic_resolver = P204SemanticArtifactResolver(
                registry=semantic_registry,
                publisher=publisher,
            )
            p204 = P204Executor(
                loader=loader,
                publisher=publisher,
                semantic_resolver=semantic_resolver,
            )
            p205 = P205Executor(
                p204=p204,
                artifact_loader=artifact_loader,
                publisher=publisher,
                selector=EvidenceCandidateSelector(reranker_client),
                unavailable_delay=timedelta(
                    seconds=config.reranker_unavailable_delay_seconds
                ),
            )
            p206 = P206Executor(
                p205=p205,
                artifact_loader=artifact_loader,
                publisher=publisher,
            )
            executor = P207Executor(
                p206=p206,
                artifact_loader=artifact_loader,
                publisher=publisher,
                current_publisher=PostgresMatchPublicationStore(publication_conn),
            )
            worker = ProcessingWorker(
                store=PostgresProcessingJobStore(queue_conn),
                executor=executor,
                worker_id=config.worker_id,
                policy=ProcessingWorkerPolicy(
                    lease_seconds=config.worker_lease_seconds,
                    idle_sleep_seconds=config.worker_idle_sleep_seconds,
                ),
            )
            ready.set()
            await worker.run_forever(stop)
    finally:
        ready.clear()
        health.close()

    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = ProcessingRuntimeConfig.from_env()

    if args.command == "check-config":
        print(json.dumps(config.safe_summary(), indent=2, sort_keys=True))
        return 0
    if args.command == "serve":
        return asyncio.run(_serve_async(config))

    raise AssertionError(f"unhandled command: {args.command}")
