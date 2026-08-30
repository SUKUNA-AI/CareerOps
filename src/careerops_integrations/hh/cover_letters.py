from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class CoverLetter:
    message: str
    strategy: str
    template_id: str
    matched_domains: tuple[str, ...]
    matched_skills: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_skill(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9+#.]+", "", value.casefold())


def _skill_names(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str):
            name = value.strip()
        elif isinstance(value, dict):
            name = str(value.get("name") or "").strip()
        else:
            name = ""
        if name:
            result.append(name)
    return result


def _matched_skills(resume: dict[str, Any], vacancy: dict[str, Any]) -> tuple[str, ...]:
    resume_skills = _skill_names(resume.get("skill_set") or [])
    vacancy_skills = _skill_names(vacancy.get("key_skills") or [])
    resume_by_norm = {_normalize_skill(skill): skill for skill in resume_skills}

    matches: list[str] = []
    seen: set[str] = set()
    for skill in vacancy_skills:
        normalized = _normalize_skill(skill)
        if not normalized or normalized not in resume_by_norm or normalized in seen:
            continue
        seen.add(normalized)
        # Prefer the vacancy spelling because it matches the employer's terminology.
        matches.append(skill)
        if len(matches) >= 3:
            break
    return tuple(matches)


def _focus_sentence(domains: tuple[str, ...]) -> str:
    domain_set = set(domains)
    if domain_set & {"llm", "vlm", "nlp"}:
        return "Особенно интересны задачи вокруг LLM/NLP/VLM и их production-интеграции."
    if "cv" in domain_set:
        return "Особенно интересны задачи компьютерного зрения и доведение моделей до рабочего пайплайна."
    if "mlops" in domain_set:
        return "Особенно интересна ML-инфраструктура: пайплайны, деплой и эксплуатация моделей."
    if "ds" in domain_set:
        return "Интересны задачи анализа данных, моделирования и доведения решений до продукта."
    if domain_set & {"ml", "dl", "ai"}:
        return "Интересны практические ML/AI-задачи и доведение решений до рабочего сервиса."
    return "Задачи вакансии близки моему текущему профессиональному направлению."


def _company_phrase(vacancy: dict[str, Any]) -> str:
    company = str((vacancy.get("employer") or {}).get("name") or "").strip()
    return f" в {company}" if company else ""


def build_cover_letter(
    *,
    vacancy: dict[str, Any],
    resume: dict[str, Any] | None,
    matched_domains: tuple[str, ...],
    seed: str,
) -> CoverLetter:
    """Build a short factual vacancy-specific cover letter without an LLM.

    The letter only uses data that came from the resume/vacancy. It does not invent
    project results, years of experience, or technologies that are absent from the
    resume. A deterministic seed makes the selected template reproducible in audit.
    """

    resume = resume or {}
    title = str(vacancy.get("name") or "вакансию").strip()
    company_phrase = _company_phrase(vacancy)
    matched_skills = _matched_skills(resume, vacancy)
    focus = _focus_sentence(matched_domains)
    resume_title = str(resume.get("title") or "").strip()

    if matched_skills:
        skills_sentence = "По стеку вижу прямое пересечение: " + ", ".join(matched_skills) + "."
    elif resume_title:
        skills_sentence = f"Мой текущий профиль в резюме — «{resume_title}», поэтому направление мне близко."
    else:
        skills_sentence = "Профиль вакансии совпадает с направлением, в котором я сейчас работаю и развиваюсь."

    templates = (
        (
            "t1",
            f"Здравствуйте! Откликаюсь на вакансию «{title}»{company_phrase}. "
            f"{focus} {skills_sentence} Буду рад обсудить задачи команды.",
        ),
        (
            "t2",
            f"Добрый день! Заинтересовала позиция «{title}»{company_phrase}. "
            f"{focus} {skills_sentence} Готов подробнее рассказать о релевантном опыте.",
        ),
        (
            "t3",
            f"Здравствуйте! Хочу откликнуться на позицию «{title}»{company_phrase}. "
            f"{skills_sentence} {focus} Буду рад познакомиться и обсудить, чем могу быть полезен команде.",
        ),
    )

    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    template_id, message = templates[digest[0] % len(templates)]
    return CoverLetter(
        message=message,
        strategy="vacancy_template_v1",
        template_id=template_id,
        matched_domains=matched_domains,
        matched_skills=matched_skills,
    )
