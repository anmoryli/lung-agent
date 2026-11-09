"""psychological_support ?? Agent ????"""

from backend.agents.patient.specialist import COMMON_RULES, PatientSpecialist

SPECIALIST = PatientSpecialist(
        name="psychological_support",
        description="提供倾听和支持选项，识别需要尽快联系医生或急救的情况。",
        system_prompt=COMMON_RULES + "\n\n先倾听并问患者更希望联系支持人员还是把情况同步给医生。此功能不是心理治疗。若提到自伤或无法保证自身安全，建议立即联系当地急救、身边可信任的人或就近急诊。不要只记录后结束。",
        read_tools=("get_patient_summary",),
        write_tools=("create_service_case", "create_task"),
    )
