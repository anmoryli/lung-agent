"""子 Agent 的流式事件要显示注册名称，不能把运行 ID 当名称。"""

from __future__ import annotations

import unittest

from backend.agents.patient.agent import PatientMainAgent


class PatientAgentTraceTests(unittest.TestCase):
    def test_root_and_child_namespace_names(self) -> None:
        self.assertEqual(PatientMainAgent._trace_agent_name(""), "patient")
        self.assertEqual(
            PatientMainAgent._trace_agent_name("task:report:run-123|model:call-456"),
            "report",
        )
        self.assertEqual(
            PatientMainAgent._trace_agent_name("parent|task:home_care_request:run-123|tools:call-456"),
            "home care request",
        )
        self.assertEqual(
            PatientMainAgent._trace_agent_name("tools:call-123|model:call-456", "report"),
            "report",
        )


if __name__ == "__main__":
    unittest.main()
