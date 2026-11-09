"""symptom_safety_triage ?? Agent ????"""

from backend.agents.patient.specialist import COMMON_RULES, PatientSpecialist

SPECIALIST = PatientSpecialist(
        name="symptom_safety_triage",
        description="处理新出现或加重的症状、紧急安全提醒和就医分流；只整理事实，不做诊断。",
        system_prompt=COMMON_RULES + "\n\n整理症状、开始时间、变化和患者目标。最多一次问两个关键问题。出现明显呼吸困难、胸痛、咯血、高热寒战、严重腹泻或自伤想法时，优先建议立即联系急救或就近医院，不建议只在家观察。",
        read_tools=("get_patient_summary", "search_observations", "search_conditions", "search_medication_requests", "search_allergies"),
    )
