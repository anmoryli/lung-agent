"""患者专科 Agent 的注册、发现和工具白名单中心。"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from backend.agents import patient as patient_package
from backend.agents.patient.specialist import PatientSpecialist


@dataclass(frozen=True)
class RegisteredSpecialist:
    definition: PatientSpecialist
    module: ModuleType


class PatientAgentRegistry:
    """每次请求重新发现各目录下的 agent.py，模型拿到的是注册表摘要。"""

    def discover(self) -> dict[str, RegisteredSpecialist]:
        discovered: dict[str, RegisteredSpecialist] = {}
        for item in pkgutil.iter_modules(patient_package.__path__):
            if item.name in {"registry", "specialist", "agent", "subagents"}:
                continue
            module_name = f"{patient_package.__name__}.{item.name}.agent"
            try:
                module = importlib.import_module(module_name)
            except ModuleNotFoundError as error:
                if error.name == module_name:
                    continue
                raise
            definition = getattr(module, "SPECIALIST", None)
            if not isinstance(definition, PatientSpecialist):
                raise TypeError(f"{module_name} 必须提供 PatientSpecialist 类型的 SPECIALIST。")
            if definition.name in discovered:
                raise ValueError(f"患者专科 Agent 名称重复：{definition.name}")
            discovered[definition.name] = RegisteredSpecialist(definition, module)
        return discovered

    def summaries(self, query: str = "") -> list[dict[str, str]]:
        """发现工具只返回职责摘要，不把子 Agent 的 MCP 工具描述全塞给主模型。"""
        terms = set(query.lower().split())
        values = []
        for name, item in self.discover().items():
            definition = item.definition
            text = f"{name} {definition.description}".lower()
            if terms and not any(term in text for term in terms):
                continue
            values.append({"name": name, "description": definition.description})
        return values

    def deepagent_specs(
        self, available_tools: dict[str, Any], patient_id: str, task_key: str,
        long_term_profile: str = "",
    ) -> list[dict[str, Any]]:
        """把已发现的 Agent 转成 DeepAgents 原生 task 工具可委派的配置。"""
        from backend.integrations.mcp.patient_fhir import WRITE_TOOLS, make_patient_tool

        subagents: list[dict[str, Any]] = []
        for item in self.discover().values():
            specialist = item.definition
            required = specialist.read_tools + specialist.write_tools
            missing = [name for name in required if name not in available_tools]
            if missing:
                raise RuntimeError("FHIR MCP 服务缺少患者流程工具：" + ", ".join(missing))
            tools = [make_patient_tool(available_tools[name], patient_id, task_key) for name in required]
            interrupt_on = {
                tool.name: {"allowed_decisions": ["approve", "reject"]}
                for tool in tools
                if tool.name in WRITE_TOOLS
            }
            subagents.append({
                "name": specialist.name,
                "description": specialist.description,
                "system_prompt": specialist.system_prompt + (
                    "\n\n当前患者的长期记忆摘要（仅供个性化沟通，过期/低权重内容不会注入）：\n" + long_term_profile
                    if long_term_profile else ""
                ),
                "tools": tools,
                "interrupt_on": interrupt_on,
                "mode": "isolated",
            })
        return subagents
