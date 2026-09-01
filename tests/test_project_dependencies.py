from __future__ import annotations

import tomllib
from pathlib import Path


def test_pytest_asyncio_is_a_dev_only_dependency() -> None:
    project_root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = config["project"]["dependencies"]
    development = config["project"]["optional-dependencies"]["dev"]

    assert not any(dependency.startswith("pytest-asyncio") for dependency in runtime)
    assert any(dependency.startswith("pytest-asyncio") for dependency in development)
