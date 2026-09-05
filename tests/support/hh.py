"""HH source payload builders shared by storage and claim tests."""

from typing import Any


def make_hh_vacancy(
    *,
    vacancy_id: str = "123",
    title: str = "ML Engineer",
    **overrides: Any,
) -> dict[str, Any]:
    """Build the storage/claim payload; replace supplied fields without repair."""
    payload: dict[str, Any] = {
        "id": vacancy_id,
        "name": title,
        "description": "<p>Python</p>",
        "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
        "employer": {"id": "10", "name": "Example"},
        "area": {"name": "Москва"},
        "relations": [],
        "archived": False,
        "closed_for_applicants": False,
        "has_test": False,
        "response_letter_required": False,
        "response_url": None,
    }
    payload.update(overrides)
    return payload
