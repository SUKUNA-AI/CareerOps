from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest


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
