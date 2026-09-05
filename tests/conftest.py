from __future__ import annotations

import asyncio
import os
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from careerops_storage.alembic_cutover import (
    TEST_POSTGRES_DSN_ENV,
    validate_disposable_postgres_dsn,
)


def pytest_asyncio_loop_factories(
    config: pytest.Config,
    item: pytest.Item,
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    """Use the psycopg-compatible Selector loop for async tests on Windows."""

    del config, item
    if sys.platform == "win32":
        return {"windows_selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(scope="session", autouse=True)
def guard_postgres_integration_target(request: pytest.FixtureRequest) -> None:
    """Fail closed before destructive PostgreSQL integration fixtures can run."""

    integration_selected = any(
        item.get_closest_marker("integration_postgres") is not None
        for item in request.session.items
    )
    if not integration_selected:
        return

    dsn = os.getenv(TEST_POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        return

    validate_disposable_postgres_dsn(dsn)


@pytest.fixture
def workspace_tmp_dir() -> Iterator[Path]:
    """Provide temp storage without relying on the restricted Windows %TEMP%."""

    root = Path.cwd() / ".careerops" / "pytest-temp"
    root.mkdir(parents=True, exist_ok=True)
    value = root / f"case-{uuid4().hex}"
    value.mkdir()
    try:
        yield value
    finally:
        shutil.rmtree(value, ignore_errors=True)
