"""DeepAgents 主 Agent 的共同调用逻辑。"""

from __future__ import annotations

import json
import os
from functools import cached_property
from typing import Any


class AgentConfigurationError(RuntimeError):
    """模型配置不完整时给 API 返回清楚的提示。"""


class RoleMainAgent:
    """一个角色主 Agent；它只整理回复，不直接调度其他 Agent。"""

    def __init__(self, role: str, system_prompt: str) -> None:
        self.role = role
        self.system_prompt = system_prompt

    @cached_property
    def graph(self) -> Any:
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise AgentConfigurationError("请先配置 OPENAI_API_KEY。")

        model = os.getenv("DEEPAGENTS_MODEL", "openai:gpt-4.1-mini").strip()
        if not model.startswith("openai:"):
            raise AgentConfigurationError(
                "当前只接入 OpenAI 兼容模型，请使用 openai:模型名 格式。"
            )

        # Gateway 是唯一编排入口，所以关掉 DeepAgents 默认的通用子 Agent。
        from deepagents import (
            GeneralPurposeSubagentProfile,
            HarnessProfile,
            create_deep_agent,
            register_harness_profile,
        )

        register_harness_profile(
            "openai",
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
                excluded_tools={
                    "delete",
                    "edit_file",
                    "execute",
                    "glob",
                    "grep",
                    "ls",
                    "read_file",
                    "write_file",
                    "write_todos",
                },
            ),
        )
        return create_deep_agent(
            model=model,
            tools=[],
            system_prompt=self.system_prompt,
        )

    def invoke(self, message: str, context: dict[str, Any]) -> str:
        """把用户问题和 Gateway 提供的上下文交给 DeepAgents。"""
        prompt = message
        if context:
            prompt += "\n\n补充业务上下文（仅根据这些已提供信息回答）：\n"
            prompt += json.dumps(context, ensure_ascii=False, indent=2)

        result = self.graph.invoke({"messages": [{"role": "user", "content": prompt}]})
        messages = result.get("messages", [])
        for item in reversed(messages):
            if getattr(item, "type", None) == "ai" or getattr(item, "role", None) == "assistant":
                text = self._message_text(getattr(item, "content", ""))
                if text:
                    return text
        raise RuntimeError("DeepAgents 没有返回文字回复。")

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return "\n".join(part for part in parts if part).strip()
        return ""
