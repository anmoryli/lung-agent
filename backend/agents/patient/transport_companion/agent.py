"""transport_companion ?? Agent ????"""

from backend.agents.patient.specialist import COMMON_RULES, PatientSpecialist

SPECIALIST = PatientSpecialist(
        name="transport_companion",
        description="查询交通或陪同相关服务信息，并在患者确认后发起预约草稿。",
        system_prompt=COMMON_RULES + "\n\n先询问日期、地点和陪同需求。只有查询到的服务信息才可展示，不承诺供应商有空。创建预约前复述信息并等待患者明确确认。",
        read_tools=("search_appointments", "search_healthcare_services", "search_organizations"),
        write_tools=("create_appointment", "create_service_case", "create_task"),
    )
