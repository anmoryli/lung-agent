"""home_care_request ?? Agent ????"""

from backend.agents.patient.specialist import COMMON_RULES, PatientSpecialist

SPECIALIST = PatientSpecialist(
        name="home_care_request",
        description="整理上门护理申请所需信息和风险项；说明申请仍需护理团队评估。",
        system_prompt=COMMON_RULES + "\n\n整理服务地点、期望时间、照护需求、过敏信息和患者是否理解后续评估。当前 FHIR 服务没有正式的上门护理申请流程，只能建立待核实草稿，不能承诺派单、费用或资格。",
        read_tools=("search_healthcare_services", "search_care_plans", "search_service_requests", "search_consents", "search_allergies"),
        write_tools=("create_service_case", "create_task"),
    )
