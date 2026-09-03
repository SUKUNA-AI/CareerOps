from careerops_integrations.hh.filtering import (
    prefilter_ml_search_item,
    validate_ml_vacancy,
)


def vacancy(name: str, **overrides):
    payload = {
        "id": "1",
        "name": name,
        "area": {"id": "1", "name": "Москва"},
        "relations": [],
        "archived": False,
        "closed_for_applicants": False,
        "has_test": False,
        "response_url": None,
        "experience": {"id": "moreThan6"},
        "description": "",
        "employer": {"name": "Example"},
    }
    payload.update(overrides)
    return payload


def test_accepts_ml_title():
    assert validate_ml_vacancy(vacancy("ML Engineer")).accepted is True


def test_accepts_senior_data_scientist_any_experience():
    assert validate_ml_vacancy(vacancy("Senior Data Scientist")).accepted is True


def test_accepts_leading_grade_data_scientist():
    assert validate_ml_vacancy(vacancy("Ведущий Data Scientist, Возвраты ML")).accepted is True


def test_accepts_senior_ml_engineer():
    assert validate_ml_vacancy(vacancy("Senior ML engineer (invest tech)")).accepted is True


def test_accepts_senior_cv_engineer():
    assert validate_ml_vacancy(vacancy("Senior Computer Vision Engineer")).accepted is True


def test_accepts_middle_ai_engineer():
    decision = validate_ml_vacancy(
        vacancy("Middle+ AI Engineer (Native Omnimodality & VLA)")
    )
    assert decision.accepted is True


def test_accepts_mlops_devops():
    decision = validate_ml_vacancy(
        vacancy("DevOps / MLOps Engineer (LLM Infrastructure)")
    )
    assert decision.accepted is True


def test_accepts_mlops_center_devops():
    decision = validate_ml_vacancy(
        vacancy("Старший DevOps - инженер (в Центр MLOps-экспертизы банка)")
    )
    assert decision.accepted is True


def test_accepts_python_ai_agents_developer():
    assert validate_ml_vacancy(vacancy("Python-разработчик AI-агентов")).accepted is True


def test_accepts_cv_specialist():
    assert validate_ml_vacancy(vacancy("Специалист по компьютерному зрению")).accepted is True


def test_accepts_hh_test_for_upstream_executor():
    assert validate_ml_vacancy(vacancy("LLM-инженер", has_test=True)).accepted is True


def test_rejects_team_lead():
    d = validate_ml_vacancy(vacancy("Team Lead Machine Learning"))
    assert d.accepted is False and "leadership" in d.blocked_terms


def test_rejects_tech_lead():
    d = validate_ml_vacancy(vacancy("Tech Lead/Senior Computer Vision Engineer"))
    assert d.accepted is False and "leadership" in d.blocked_terms


def test_rejects_plain_lead_data_scientist():
    d = validate_ml_vacancy(vacancy("Lead Data Scientist"))
    assert d.accepted is False and "leadership" in d.blocked_terms


def test_rejects_timlead():
    d = validate_ml_vacancy(vacancy("Тимлид Data Science/ML"))
    assert d.accepted is False and "leadership" in d.blocked_terms


def test_rejects_product_manager_cv():
    d = validate_ml_vacancy(vacancy("Менеджер продукта (Computer Vision)"))
    assert d.accepted is False and "product_project" in d.blocked_terms


def test_rejects_product_owner_mlops():
    d = validate_ml_vacancy(vacancy("Product Owner/Senior MLOps"))
    assert d.accepted is False and "product_project" in d.blocked_terms


def test_rejects_system_analyst_mlops():
    d = validate_ml_vacancy(vacancy("Системный аналитик MLOps / AI-агенты"))
    assert d.accepted is False and "system_business_analyst" in d.blocked_terms


def test_rejects_ai_content_specialist():
    d = validate_ml_vacancy(vacancy("AI-специалист по управлению контентом"))
    assert d.accepted is False and "content_ops" in d.blocked_terms


def test_rejects_ai_site_specialist():
    d = validate_ml_vacancy(vacancy("ИИ-Специалист по созданию сайтов"))
    assert d.accepted is False and "web_site" in d.blocked_terms


def test_rejects_ai_ux_ui():
    d = validate_ml_vacancy(vacancy("AI-инженер / UX/UI Engineer"))
    assert d.accepted is False and "design" in d.blocked_terms


def test_rejects_plain_devops_ai():
    d = validate_ml_vacancy(vacancy("DevOps-инженер (Senior), AI платформа"))
    assert d.accepted is False and d.reason == "devops_without_mlops"


def test_rejects_devops_genai_without_mlops():
    d = validate_ml_vacancy(vacancy("DevOps-инженер Generative AI"))
    assert d.accepted is False and d.reason == "devops_without_mlops"


def test_rejects_ios_ai():
    assert validate_ml_vacancy(vacancy("iOS разработчик в AI-команду")).accepted is False


def test_rejects_csharp_ml():
    assert validate_ml_vacancy(vacancy("C#/.NET разработчик ML Platform")).accepted is False


def test_rejects_unity_ai():
    assert validate_ml_vacancy(vacancy("Senior Unity/AI Developer")).accepted is False


def test_rejects_quality_control_cv():
    decision = validate_ml_vacancy(
        vacancy("Инженер по контролю качества кода и моделей (Computer Vision)")
    )
    assert decision.accepted is False


def test_rejects_generic_ai_specialist_without_engineering_role():
    d = validate_ml_vacancy(vacancy("AI-специалист"))
    assert d.accepted is False and d.reason == "generic_ai_non_engineering_title"


def test_rejects_drones_in_title_prefilter():
    d = prefilter_ml_search_item({"id": "1", "name": "Computer Vision Engineer БПЛА"})
    assert d.accepted is False and "drones_uav" in d.blocked_terms


def test_rejects_donetsk_in_title_prefilter():
    d = prefilter_ml_search_item(
        {"id": "1", "name": "Специалист по компьютерному зрению (Донецк на 6 месяцев) далее Москва"}
    )
    assert d.accepted is False and "war_region" in d.blocked_terms


def test_rejects_military_context_in_description():
    d = validate_ml_vacancy(
        vacancy("ML Engineer", description="Разработка моделей для оборонной промышленности")
    )
    assert d.accepted is False and "military_defence" in d.blocked_terms


def test_rejects_drone_context_in_description():
    d = validate_ml_vacancy(
        vacancy("Computer Vision Engineer", description="CV для беспилотных летательных аппаратов")
    )
    assert d.accepted is False and "drones_uav" in d.blocked_terms


def test_rejects_relocation_context_in_description():
    d = validate_ml_vacancy(
        vacancy("Data Scientist", description="Предоставляем релокацию и переезд в другой регион")
    )
    assert d.accepted is False and "relocation" in d.blocked_terms


def test_rejects_rotational_shift_context():
    d = validate_ml_vacancy(vacancy("ML Engineer", description="Работа вахтовым методом"))
    assert d.accepted is False and "relocation" in d.blocked_terms


def test_prefilter_rejects_teamlead_without_full_fetch():
    d = prefilter_ml_search_item({"id": "1", "name": "Тимлид Data Science/ML"})
    assert d.accepted is False


def test_prefilter_accepts_senior_ml_without_experience_check():
    d = prefilter_ml_search_item({"id": "1", "name": "Senior ML Engineer"})
    assert d.accepted is True


def test_external_response_still_blocks_after_full_fetch():
    d = validate_ml_vacancy(vacancy("ML Engineer", response_url="https://example.com"))
    assert d.accepted is False and d.reason == "external_response_url"


def test_prefilter_treats_global_relations_as_non_resume_specific():
    d = prefilter_ml_search_item(
        {"id": "1", "name": "ML Engineer", "relations": ["got_response"]}
    )
    assert d.accepted is True and d.reason == "accepted"


def test_full_validation_treats_global_relations_as_non_resume_specific():
    d = validate_ml_vacancy(
        vacancy("ML Engineer", relations=["got_response"])
    )
    assert d.accepted is True and d.reason == "accepted"
