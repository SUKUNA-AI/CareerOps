from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[1]
V2_BASELINE_REVISION = "20260906_v2_0001"


def _config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _parent_revisions(value: str | tuple[str, ...] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return value


def test_v2_alembic_revision_graph_has_one_root_and_one_head() -> None:
    script = ScriptDirectory.from_config(_config())

    assert script.get_bases() == [V2_BASELINE_REVISION]
    heads = script.get_heads()
    assert len(heads) == 1

    revisions = tuple(script.walk_revisions(base="base", head="heads"))
    revision_ids = {revision.revision for revision in revisions}
    assert V2_BASELINE_REVISION in revision_ids
    assert heads[0] in revision_ids

    baseline = script.get_revision(V2_BASELINE_REVISION)
    assert baseline is not None
    assert baseline.down_revision is None
    assert baseline.branch_labels is not None
    assert "v2" in baseline.branch_labels

    for revision in revisions:
        for parent in _parent_revisions(revision.down_revision):
            assert parent in revision_ids
            assert script.get_revision(parent) is not None


def test_v2_offline_upgrade_does_not_require_runtime_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CAREEROPS_V2_POSTGRES_DSN", raising=False)
    monkeypatch.setenv("CAREEROPS_POSTGRES_DSN", "sqlite:///must-not-win")

    output = StringIO()
    config = _config()
    config.output_buffer = output
    command.upgrade(config, "head", sql=True)

    rendered = output.getvalue()
    assert "careerops_v2" in rendered
    assert "alembic_version_v2" in rendered
