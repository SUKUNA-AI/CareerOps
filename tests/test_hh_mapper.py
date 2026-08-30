from datetime import UTC, datetime

from careerops_contracts import RawVacancyRef
from careerops_integrations.hh.mapper import extract_operational, map_hh_vacancy


def test_maps_realistic_hh_vacancy():
    raw = RawVacancyRef(
        source="hh",
        source_entity_id="136655995",
        raw_uri="file:///tmp/vacancy.json",
        content_hash="a" * 64,
        collected_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
    )
    payload = {
        "id": "136655995",
        "name": "Junior ML engineer",
        "relations": ["got_response"],
        "response_letter_required": False,
        "area": {"id": "1", "name": "Москва"},
        "salary": None,
        "experience": {"id": "between1And3"},
        "schedule": {"id": "fullDay"},
        "employment": {"id": "full"},
        "description": "<p>Python <strong>SQL</strong></p>",
        "archived": False,
        "response_url": None,
        "employer": {"id": "4233", "name": "Х5"},
        "published_at": "2026-08-26T17:40:52+0300",
        "has_test": False,
        "alternate_url": "https://hh.ru/vacancy/136655995",
        "work_format": [{"id": "ON_SITE"}, {"id": "HYBRID"}],
        "closed_for_applicants": False,
    }

    canonical = map_hh_vacancy(payload, raw=raw)
    operational = extract_operational(payload)

    assert canonical.company_name == "Х5"
    assert canonical.title == "Junior ML engineer"
    assert canonical.remote is False
    assert canonical.experience == "between1And3"
    assert "Python" in (canonical.description or "")
    assert operational.relations == ("got_response",)
    assert operational.already_interacted is True
