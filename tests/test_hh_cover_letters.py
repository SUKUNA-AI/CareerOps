from careerops_integrations.hh.cover_letters import build_cover_letter


def _vacancy(name: str, *, company: str = "Example", skills=None):
    return {
        "id": "1",
        "name": name,
        "employer": {"name": company},
        "key_skills": [{"name": x} for x in (skills or [])],
    }


def test_cover_letter_contains_vacancy_and_company():
    letter = build_cover_letter(
        vacancy=_vacancy("ML Engineer", company="Acme"),
        resume={"title": "ML-инженер", "skill_set": ["Python"]},
        matched_domains=("ml",),
        seed="run:1",
    )
    assert "ML Engineer" in letter.message
    assert "Acme" in letter.message
    assert letter.strategy == "vacancy_template_v1"


def test_cover_letter_uses_only_real_skill_intersection():
    letter = build_cover_letter(
        vacancy=_vacancy("LLM Engineer", skills=["Python", "PyTorch", "Kubernetes"]),
        resume={"title": "ML-инженер", "skill_set": ["Python", "PyTorch", "Docker"]},
        matched_domains=("llm",),
        seed="run:2",
    )
    assert letter.matched_skills == ("Python", "PyTorch")
    assert "Kubernetes" not in letter.message


def test_domain_changes_focus():
    cv = build_cover_letter(
        vacancy=_vacancy("Computer Vision Engineer"),
        resume={"title": "ML-инженер"},
        matched_domains=("cv",),
        seed="same",
    )
    mlops = build_cover_letter(
        vacancy=_vacancy("MLOps Engineer"),
        resume={"title": "ML-инженер"},
        matched_domains=("mlops",),
        seed="same",
    )
    assert "компьютерного зрения" in cv.message
    assert "ML-инфраструктура" in mlops.message
