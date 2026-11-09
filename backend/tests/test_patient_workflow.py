"""患者工作流的关键安全边界回归。"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from pydantic import BaseModel

from backend.agents.patient.agent import PatientMainAgent
from backend.agents.patient.registry import PatientAgentRegistry
from backend.integrations.mcp.patient_fhir import make_patient_tool, patient_id_from_context
from backend.runtime.async_runner import run_async


class WriteInput(BaseModel):
    patient_id: str
    task_key: str
    confirmed: bool = False
    doctor_confirmed: bool = False
    description: str


class FakeWriteTool:
    name = "create_task"
    description = "创建演示任务"
    args_schema = WriteInput

    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None

    async def ainvoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payload = payload
        return payload


class FakeInterrupt:
    def __init__(self, interrupt_id: str) -> None:
        self.id = interrupt_id


class FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, str]] = {}

    def put(self, namespace: tuple[str, ...], key: str, value: dict[str, str]) -> None:
        self.items[(namespace, key)] = value

    def delete(self, namespace: tuple[str, ...], key: str) -> None:
        self.items.pop((namespace, key), None)


class PatientWorkflowTests(unittest.TestCase):
    def test_agui_resume_matches_deepagents_hitl_decision_shape(self) -> None:
        interrupt = FakeInterrupt("interrupt-1")
        approved = PatientMainAgent._agui_interrupt_decisions(
            [interrupt],
            [{"interrupt_id": "interrupt-1", "status": "resolved", "payload": {"approved": True}}],
        )
        rejected = PatientMainAgent._agui_interrupt_decisions(
            [interrupt],
            [{"interrupt_id": "interrupt-1", "status": "resolved", "payload": {"approved": False}}],
        )
        self.assertEqual(approved, {"interrupt-1": {"decisions": [{"type": "approve"}]}})
        self.assertEqual(rejected, {"interrupt-1": {"decisions": [{"type": "reject", "message": "患者没有同意这次写入。"}]}})

    def test_urgent_phrases_bypass_model_and_negated_phrase_does_not(self) -> None:
        self.assertTrue(PatientMainAgent._needs_urgent_care("我现在在咳血"))
        self.assertTrue(PatientMainAgent._needs_urgent_care("我想自杀"))
        self.assertFalse(PatientMainAgent._needs_urgent_care("我没有胸痛"))
        self.assertTrue(PatientMainAgent._needs_urgent_care("我没有胸痛，但现在胸痛很严重"))

    def test_patient_id_only_accepts_synthetic_demo_ids(self) -> None:
        self.assertEqual(patient_id_from_context({"patient_id": "demo-patient-0002"}), "demo-patient-0002")
        with self.assertRaises(ValueError):
            patient_id_from_context({"patient_id": "patient-1"})

    def test_conversation_checkpoint_is_scoped_to_patient(self) -> None:
        first = PatientMainAgent._thread_id({"conversationId": "same"}, "demo-patient-0001", "turn-1")
        second = PatientMainAgent._thread_id({"conversationId": "same"}, "demo-patient-0002", "turn-1")
        self.assertNotEqual(first, second)

    def test_eight_specialists_are_found_through_the_registry(self) -> None:
        specialists = PatientAgentRegistry().discover()
        self.assertEqual(len(specialists), 8)
        self.assertTrue(all(item.definition.read_tools for item in specialists.values()))
        self.assertEqual(specialists["symptom_safety_triage"].definition.write_tools, ())

    def test_write_tool_binds_patient_and_confirmation_fields(self) -> None:
        raw = FakeWriteTool()
        tool = make_patient_tool(raw, "demo-patient-0001", "turn-22")
        self.assertNotIn("patient_id", tool.args_schema.model_fields)
        self.assertNotIn("confirmed", tool.args_schema.model_fields)
        result = asyncio.run(tool.ainvoke({"description": "患者同意的演示任务"}))
        self.assertEqual(result["patient_id"], "demo-patient-0001")
        self.assertEqual(result["task_key"], "turn-22:create_task")
        self.assertTrue(result["confirmed"])
        self.assertFalse(result["doctor_confirmed"])

    def test_async_runner_reuses_one_loop_between_requests(self) -> None:
        async def loop_id() -> int:
            return id(asyncio.get_running_loop())

        first = run_async(loop_id())
        second = run_async(loop_id())
        self.assertEqual(first, second)

    def test_memory_reads_by_default_and_can_be_disabled(self) -> None:
        self.assertTrue(PatientMainAgent._memory_enabled({}))
        self.assertTrue(PatientMainAgent._memory_enabled({"memoryConsent": True}))
        self.assertFalse(PatientMainAgent._memory_enabled({"memoryConsent": False}))


if __name__ == "__main__":
    unittest.main()
