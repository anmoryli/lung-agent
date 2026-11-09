"""exam_order ?? Agent ????"""

from backend.agents.patient.specialist import COMMON_RULES, PatientSpecialist

SPECIALIST = PatientSpecialist(
        name="exam_order",
        description="整理已有检查、医嘱和预约；患者询问是否需要检查时转医生确认。",
        system_prompt=COMMON_RULES + "\n\n仅展示已有医嘱、检查和预约状态。可以整理待确认草稿，不能自行决定检查项目、下医嘱或安排治疗。",
        read_tools=("search_service_requests", "search_medication_requests", "search_appointments", "search_observations"),
        write_tools=("create_service_request_draft", "create_appointment"),
    )
