"""consultation_prep ?? Agent ????"""

from backend.agents.patient.specialist import COMMON_RULES, PatientSpecialist

SPECIALIST = PatientSpecialist(
        name="consultation_prep",
        description="帮助患者准备本次问诊目标、问题清单和已有资料。",
        system_prompt=COMMON_RULES + "\n\n先确认本次想解决的问题和线上或线下就诊方式。只整理患者实际提供的目标，不提供诊疗建议。",
        read_tools=("get_patient_summary", "search_encounters", "search_reports", "search_documents", "search_service_requests", "search_appointments"),
    )
