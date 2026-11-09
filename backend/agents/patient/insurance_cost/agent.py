"""insurance_cost ?? Agent ????"""

from backend.agents.patient.specialist import COMMON_RULES, PatientSpecialist

SPECIALIST = PatientSpecialist(
        name="insurance_cost",
        description="查看已登记的医保覆盖信息并说明费用信息缺口；不承诺报销金额。",
        system_prompt=COMMON_RULES + "\n\n只汇报系统已有的医保覆盖信息。当前系统没有账单明细和报销计算能力，不能编造费用、报销比例或结论；需要核实的事项可以整理成待确认服务请求。",
        read_tools=("search_coverages", "search_encounters"),
        write_tools=("create_service_case", "create_task"),
    )
