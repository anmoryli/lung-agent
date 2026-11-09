"""exam_order ?? Agent ????"""

from backend.agents.patient.specialist import COMMON_RULES, PatientSpecialist

SPECIALIST = PatientSpecialist(
        name="exam_order",
        description="查询已有检查和预约；按患者明确需求提交预约申请或检查草稿。",
        system_prompt=COMMON_RULES + "\n\n患者明确要求挂号或指定科室时，直接按新预约申请处理，不先查已有预约，也不展示无关预约。只有患者明确询问已有预约或状态时才查询。患者在本轮已经说清科室并答应提交后，直接调用 create_appointment，不再追问或重复征求同意。没说科室且无法从上下文得知时，只问科室这一个必要问题。当前不能查询号源，不得编造号源或承诺可约时间；可以根据当前日期提交近期意向，时间明确标成待医院安排。预约写入后状态为待医院确认，不代表挂号成功。提交前说明一次，由确认卡完成确认。检查项目由医生决定，不能自行添加医嘱。",
        read_tools=("search_service_requests", "search_medication_requests", "search_appointments", "search_observations"),
        write_tools=("create_service_request_draft", "create_appointment"),
    )
