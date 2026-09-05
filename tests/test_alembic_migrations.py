from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory

from careerops_storage.alembic_cutover import (
    BASELINE_REVISION,
    PROJECT_ROOT,
    get_revisions_after,
    get_single_alembic_head,
)


def _config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _parent_revisions(value: str | tuple[str, ...] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return value


def test_alembic_revision_graph_has_one_root_one_reachable_head() -> None:
    config = _config()
    script = ScriptDirectory.from_config(config)
    bases = script.get_bases()
    heads = script.get_heads()

    assert bases == [BASELINE_REVISION]
    assert len(heads) == 1
    head = get_single_alembic_head(config)
    assert head == heads[0]

    revisions = tuple(script.walk_revisions(base="base", head="heads"))
    revision_ids = {revision.revision for revision in revisions}
    assert BASELINE_REVISION in revision_ids
    assert head in revision_ids

    baseline = script.get_revision(BASELINE_REVISION)
    assert baseline is not None
    assert baseline.down_revision is None
    assert not baseline.branch_labels

    for revision in revisions:
        for parent in _parent_revisions(revision.down_revision):
            assert parent in revision_ids
            assert script.get_revision(parent) is not None

    ancestry = {
        revision.revision
        for revision in script.iterate_revisions(head, "base")
    }
    assert {BASELINE_REVISION, head}.issubset(ancestry)
    assert set(get_revisions_after(config, BASELINE_REVISION)) == (
        ancestry - {BASELINE_REVISION}
    )
