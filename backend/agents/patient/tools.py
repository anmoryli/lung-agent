"""患者主 Agent 的小工具集；具体 FHIR 工具只交给被选中的专科 Agent。"""

from __future__ import annotations

import json
from typing import Callable

from backend.agents.patient.registry import PatientAgentRegistry


def create_patient_tools(
    registry: PatientAgentRegistry,
    search_history: Callable[[str], list[dict]],
    remember: Callable[..., None],
    memory_enabled: bool = True,
) -> list[object]:
    """用 LangChain 的 @tool 注册机制，把发现和记忆检索交给主模型。"""
    from langchain_core.tools import tool

    @tool
    def discover_patient_agents(query: str = "") -> str:
        """查看当前已注册的患者专科 Agent 及职责，分流前先查这里。"""
        return json.dumps(registry.summaries(query), ensure_ascii=False)

    tools = [discover_patient_agents]
    if not memory_enabled:
        return tools

    @tool
    def search_patient_history(query: str) -> str:
        """按语义查找患者之前的对话片段；仅在历史信息能帮助当前问题时调用。"""
        return json.dumps(search_history(query), ensure_ascii=False)

    @tool
    def save_patient_memory(
        content: str,
        category: str = "preference",
        importance: int = 3,
        expires_in_days: int | None = None,
        supersedes_id: str = "",
        evidence: str = "",
        conflicts_with_id: str = "",
    ) -> str:
        """保存明确要求记住的非临床事实；可设重要度、有效期、替代关系或未解决冲突。"""
        remember(content, category, importance, expires_in_days, supersedes_id, evidence, conflicts_with_id)
        return "已写入独立长期记忆，并更新每轮注入的短摘要。"

    return [*tools, search_patient_history, save_patient_memory]
