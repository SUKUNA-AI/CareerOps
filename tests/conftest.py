from __future__ import annotations

import asyncio
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from support.postgres import PostgresTestTarget, load_postgres_test_target


def pytest_asyncio_loop_factories(
    config: pytest.Config,
    item: pytest.Item,
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    """Use the psycopg-compatible Selector loop for async tests on Windows."""

    del config, item
    if sys.platform == "win32":
        return {"windows_selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


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


@pytest.fixture
def postgres_test_target() -> PostgresTestTarget:
    """Expose one guarded disposable target to all real PostgreSQL integration tests."""

    target = load_postgres_test_target()
    if target is None:
        pytest.skip("CAREEROPS_TEST_POSTGRES_DSN is not configured")
    return target
