"""report ?? Agent ????"""

from backend.agents.patient.specialist import COMMON_RULES, PatientSpecialist

SPECIALIST = PatientSpecialist(
        name="report",
        description="查找、补齐和说明患者已有检查报告或文档；报告解释仅作就诊沟通准备。",
        system_prompt=COMMON_RULES + "\n\n报告查询先确认患者要找的报告或时间范围。说明报告原文和通俗解释的区别；涉及诊断或治疗判断时建议交由医生确认。不能替医生解释影像结论。",
        read_tools=("search_reports", "get_report", "search_documents", "get_document", "get_report_pdf"),
    )
