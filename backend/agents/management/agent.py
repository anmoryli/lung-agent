"""管理主 Agent。"""

from backend.agents.base import RoleMainAgent


class ManagementMainAgent(RoleMainAgent):
    def __init__(self) -> None:
        super().__init__(
            role="management",
            system_prompt="""
你是管理主 Agent，帮助管理人员核对服务资料、安排协同事项、整理异常和 SLA 草稿。
只依据请求中给出的服务规则和事实回答；没有规则时指出缺少什么，不承诺服务、费用或完成时间。
你不能作出医疗判断、修改患者诊疗计划、付款、调用外部系统或把草稿当成已执行结果。
需要写回或联系他人时，只提供待人工确认的草稿和下一步。
""".strip(),
        )
