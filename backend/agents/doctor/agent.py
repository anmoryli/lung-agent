"""医生主 Agent。"""

from backend.agents.base import RoleMainAgent


class DoctorMainAgent(RoleMainAgent):
    def __init__(self) -> None:
        super().__init__(
            role="doctor",
            system_prompt="""
你是医生主 Agent，帮助医生整理患者资料、报告和症状信息，生成摘要或文书草稿。
明确区分已知事实、患者陈述、资料缺失和待核实内容；不补造病史、检查值或证据来源。
你可以提供辅助分析和候选问题，但不能代替医生作出诊断、分期、治疗选择或医嘱。
凡涉及医疗判断，明确标为待医生确认；表达简洁、结构清晰。
""".strip(),
        )
