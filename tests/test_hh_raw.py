from uuid import UUID
from careerops_integrations.hh.raw import LocalRawStore


def test_local_raw_store(tmp_path):
    ref = LocalRawStore(tmp_path).write(
        payload={"id": "1", "name": "ML Engineer"},
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        vacancy_id="1",
    )
    assert ref.source == "hh"
    assert len(ref.content_hash) == 64
    assert ref.raw_uri.startswith("file:")
