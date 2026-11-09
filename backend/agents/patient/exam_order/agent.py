"""exam_order ?? Agent ????"""

from backend.agents.patient.specialist import COMMON_RULES, PatientSpecialist

SPECIALIST = PatientSpecialist(
        name="exam_order",
        description="查询已有检查和预约；按患者明确需求提交预约申请或检查草稿。",
        system_prompt=COMMON_RULES + "\n\n挂号请求先查已有预约。若患者已有相符的近期预约，且患者没有明确要求新增预约，直接用一句话告知最近这一条并说明如何提出新需求；不要提问，不要列其他科室、护理预约或同日检查。例如：‘您已经有一条肿瘤复诊预约（照实填写查到的日期和时间）。如果还要另挂，告诉我科室就行。’只有患者明确要新挂号且资料缺少必要信息时才问一个关键项。当前没有号源查询能力，不得编造可约时间。创建的是待医院确认的预约申请，不能说已经挂号成功。提交前说明一次，随后由确认卡完成确认。检查项目是否必要由医生决定，不能自行添加医嘱。",
        read_tools=("search_service_requests", "search_medication_requests", "search_appointments", "search_observations"),
        write_tools=("create_service_request_draft", "create_appointment"),
    )
