"""按角色选择主 Agent，并统一处理 taskKey。"""

from __future__ import annotations

from backend.agents.doctor.agent import DoctorMainAgent
from backend.agents.management.agent import ManagementMainAgent
from backend.agents.nursing.agent import NursingMainAgent
from backend.agents.patient.agent import PatientMainAgent
from backend.shared.tasking.idempotency_store import IdempotencyStore


class UnknownRoleError(ValueError):
    """请求了未配置的主 Agent。"""


class Gateway:
    def __init__(self) -> None:
        self.agents = {
            "patient": PatientMainAgent(),
            "doctor": DoctorMainAgent(),
            "management": ManagementMainAgent(),
            "nursing": NursingMainAgent(),
        }
        self.idempotency = IdempotencyStore()

    def get_agent(self, role: str):
        """取出 Gateway 已注册的角色主 Agent，外部路由不直接挑选实现类。"""
        agent = self.agents.get(role)
        if agent is None:
            raise UnknownRoleError(role)
        return agent

    def invoke(
        self,
        role: str,
        task_key: str,
        message: str,
        context: dict,
    ) -> tuple[str, bool]:
        agent = self.get_agent(role)
        agent_context = context
        if role == "patient":
            agent_context = {**context, "_task_key": task_key}
        return self.idempotency.run_once(
            task_key,
            role,
            message,
            context,
            lambda: agent.invoke(message, agent_context),
        )
