"""时间上下文及主动查询工具的行为回归。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.agents.patient.registry import PatientAgentRegistry
from backend.agents.patient.tools import create_patient_tools
from backend.agents.patient.agent import PatientMainAgent
from backend.runtime.clock import current_time_context
from backend.runtime.patient_memory_worker import PatientMemoryExtractionWorker


class ClockTests(unittest.TestCase):
    def test_current_time_uses_configured_timezone(self) -> None:
        with patch.dict(os.environ, {"AGENT_TIMEZONE": "Asia/Shanghai"}):
            result = current_time_context()
        self.assertIn("Asia/Shanghai", result)
        self.assertIn("+0800", result)
        self.assertIn("回答今天、明天、昨天", result)

    def test_time_tool_is_available_when_memory_is_disabled(self) -> None:
        tools = create_patient_tools(
            PatientAgentRegistry(), lambda _query: [], lambda *_args: None,
            memory_enabled=False,
        )
        time_tool = next(tool for tool in tools if tool.name == "get_current_time")
        result = time_tool.invoke({})
        self.assertIn("当前时间：", result)
        self.assertIn("Asia/Shanghai", result)

    def test_main_and_memory_extraction_prompts_include_time(self) -> None:
        context = "当前时间：2026-11-09 16:00:00 +0800（Asia/Shanghai）。"
        main_prompt = PatientMainAgent._prompt_with_context([], "", context)
        memory_prompt = PatientMemoryExtractionWorker._prompt("用户原话", [], context)
        self.assertIn(context, main_prompt)
        self.assertIn(context, memory_prompt)


if __name__ == "__main__":
    unittest.main()
