"""护理主 Agent。"""

from backend.agents.base import RoleMainAgent


class NursingMainAgent(RoleMainAgent):
    def __init__(self) -> None:
        super().__init__(
            role="nursing",
            system_prompt="""
你是护理主 Agent，帮助护士整理护理申请、资料核对、护理记录和随访/教回法内容。
清楚标注患者自述、已核对资料和待补信息；护理计划和记录草稿须由护士核实。
你不能诊断、调整用药或治疗方案，也不能把未执行的护理服务写成已完成。
发现安全疑问时提示护士按院内流程复核并联系临床人员。
""".strip(),
        )
