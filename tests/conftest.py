from __future__ import annotations

import asyncio
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from support.postgres import (
    PostgresTestTarget,
    assert_v2_test_database_empty,
    load_postgres_test_target,
    reset_v2_test_database,
)

V2_POSTGRES_DSN_ENV = "CAREEROPS_V2_POSTGRES_DSN"


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
    """Return the guarded dedicated PostgreSQL test target or skip local execution."""

    target = load_postgres_test_target()
    if target is None:
        pytest.skip("CAREEROPS_TEST_POSTGRES_DSN is not configured")
    return target


@pytest.fixture
def v2_postgres_test_target(
    postgres_test_target: PostgresTestTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[PostgresTestTarget]:
    """Provide a clean v2 database and pin Alembic to the already validated target."""

    monkeypatch.setenv(V2_POSTGRES_DSN_ENV, postgres_test_target.dsn)
    monkeypatch.setenv("CAREEROPS_POSTGRES_DSN", "sqlite:///must-not-win")
    reset_v2_test_database(postgres_test_target)
    assert_v2_test_database_empty(postgres_test_target)
    try:
        yield postgres_test_target
    finally:
        reset_v2_test_database(postgres_test_target)
