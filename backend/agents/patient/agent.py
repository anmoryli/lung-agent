"""患者主 Agent：Gateway 内的唯一患者工作流入口。"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from functools import cached_property
from typing import Any, Callable

from backend.agents.base import AgentConfigurationError, RoleMainAgent
from backend.agents.patient.registry import PatientAgentRegistry
from backend.agents.patient.tools import create_patient_tools
from backend.integrations.memory.vector_store import PatientMemoryVectorStore
from backend.integrations.mcp.patient_fhir import mcp_transport, patient_id_from_context
from backend.runtime.async_runner import run_async


PATIENT_PROMPT = """
你是基于 DeepAgents 的肺癌患者服务助手。先判断患者现在要办什么，再决定直接回答还是交给一个最合适的专科助手。

工作顺序：
1. 打招呼时简短回应，不罗列功能清单，也不主动输出与当前问题无关的急症提醒。识别目标后只处理患者当前要办的事。
2. 需要病历事实时，只让专科助手查当前合成患者的资料。资料里没有的内容就说暂时查不到。
3. 先用病历、当前会话和已有信息补齐上下文，不让患者重复提供系统已有的信息。确实缺少阻塞下一步的内容时，每轮只问一个最关键的问题；能用安全默认值继续时就直接继续，不连环追问。
4. 如果患者只泛称“挂号/预约”，而资料里已有未来的同类已确认预约，就只告知最近一条预约的科室、时间和状态，然后结束回复；不要追问是否要新挂、不要索要更多字段。患者要新增预约会继续提出。
5. 一次只委派一个专科助手，不让子助手互相委派。预约请求只回答相关预约，别把同日检查、护理或其他无关安排一并列出。
6. 写入工具会弹出一次确认。先用一句话说明将提交的内容，再调用写入工具等待确认；不要在工具确认前另行追问一次口头同意。预约申请的状态是“待医院确认”，不能说成已挂号成功。
7. 输出简短、易懂的当前结论和下一步。医疗判断由医生确认；不诊断、不建议停换药、不决定检查必要性，不承诺资格、费用或服务可用。

患者提到严重呼吸困难、胸痛、咯血、高热寒战、严重腹泻或自伤想法时，优先建议立即联系当地急救或就近急诊。不要把高风险情况说成在家观察即可，也不要输出内部风险标签、工具名、Agent 名或推理过程。

默认从当前患者自己的对话归档中按语义召回相关内容，并归档新的对话。只允许在同一合成患者编号下检索；患者明确要求不再保存时停止归档并清除该患者的向量记忆。

记忆默认可读取，不再询问授权。患者明确要求“记住”时，可以直接保存沟通偏好和服务安排；长期记忆不保存症状、报告、诊断或用药。
""".strip()

URGENT_TERMS = (
    "严重呼吸困难", "呼吸困难", "喘不上气", "胸痛", "胸口痛", "咯血", "咳血",
    "高热", "高烧", "寒战", "严重腹泻", "想自杀", "自杀想法", "不想活",
    "伤害自己", "轻生",
)
NEGATIONS = ("没有", "否认", "未出现", "不伴", "不再", "无", "没出现")
CLAUSE_BREAKS = ("。", "，", "；", "！", "？", ",", ";", "!", "?", "但是", "不过", "但")


class PatientMainAgent(RoleMainAgent):
    """患者用的 DeepAgents 编排器，保留其他角色原有调用方式。"""

    def __init__(self) -> None:
        super().__init__(role="patient", system_prompt=PATIENT_PROMPT)

    def invoke(self, message: str, context: dict[str, Any]) -> str:
        if self._needs_urgent_care(message):
            return "你提到了需要尽快处理的情况。如果这些症状现在正在发生，或你担心自己会受伤，请马上联系当地急救或去最近的急诊；也请让身边可信任的人陪着你。"
        return run_async(self._invoke_patient(message, context))

    def invoke_stream(self, message: str, context: dict[str, Any], on_delta: Callable[[str], None]) -> str:
        if self._needs_urgent_care(message):
            reply = "你提到了需要尽快处理的情况。如果这些症状现在正在发生，或你担心自己会受伤，请马上联系当地急救或去最近的急诊；也请让身边可信任的人陪着你。"
            on_delta(reply)
            return reply
        return run_async(self._invoke_patient(message, context, on_delta))

    @staticmethod
    def _needs_urgent_care(message: str) -> bool:
        """按 PRD 的明确急症词先分流；紧急提醒不等模型判断。"""
        for term in URGENT_TERMS:
            position = message.find(term)
            while position >= 0:
                start = max(
                    (message.rfind(separator, 0, position) + len(separator) for separator in CLAUSE_BREAKS),
                    default=0,
                )
                prefix = message[max(start, position - 14):position]
                if not any(negation in prefix for negation in NEGATIONS):
                    return True
                position = message.find(term, position + len(term))
        return False

    @staticmethod
    def _is_generic_booking_request(message: str) -> bool:
        """泛泛说要挂号时先查是否已经有号，避免把患者带进重复预约流程。"""
        text = re.sub(r"\s+", "", message)
        if any(term in text for term in ("取消", "改期", "改约", "新挂", "再挂", "另外挂", "查询预约")):
            return False
        return any(term in text for term in ("帮我挂个号", "帮我挂号", "帮我预约挂号", "能不能帮我挂个号", "可以帮我挂个号"))

    @staticmethod
    def _existing_clinic_appointment(tool_result: Any) -> str | None:
        """从 MCP 搜索结果里挑最近一条未来肿瘤复诊号，忽略护理随访等无关预约。"""
        bundles = []
        if isinstance(tool_result, dict):
            bundles.append(tool_result)
        elif isinstance(tool_result, list):
            for item in tool_result:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    try:
                        bundles.append(json.loads(item["text"]))
                    except json.JSONDecodeError:
                        continue

        now = datetime.now(timezone.utc)
        matches = []
        for bundle in bundles:
            for entry in bundle.get("entry", []):
                resource = entry.get("resource", {})
                if resource.get("status") != "booked":
                    continue
                service_names = [
                    value
                    for service in resource.get("serviceType", [])
                    for value in [service.get("text", ""), *(coding.get("display", "") for coding in service.get("coding", []))]
                    if value
                ]
                service_name = "、".join(dict.fromkeys(service_names))
                if not any(term in service_name for term in ("肿瘤", "复诊")):
                    continue
                try:
                    start = datetime.fromisoformat(resource["start"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(resource["end"].replace("Z", "+00:00"))
                except (KeyError, ValueError):
                    continue
                if start >= now:
                    matches.append((start, end, service_name))

        if not matches:
            return None
        start, end, service_name = min(matches, key=lambda item: item[0])
        weekdays = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
        return (
            f"您已有一条{service_name}预约：{start.month}月{start.day}日（{weekdays[start.weekday()]}）"
            f"{start:%H:%M}–{end:%H:%M}，状态为已确认。"
        )

    async def _invoke_patient(
        self,
        message: str,
        context: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
        on_interrupt: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> str:
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise AgentConfigurationError("请先配置 OPENAI_API_KEY。")
        model = os.getenv("DEEPAGENTS_MODEL", "openai:gpt-4.1-mini").strip()
        if not model.startswith("openai:"):
            raise AgentConfigurationError("当前只接入 OpenAI 兼容模型，请使用 openai:模型名 格式。")

        patient_id = patient_id_from_context(context)
        task_key = str(context.get("_task_key", "patient-task"))[:128]
        thread_id = self._thread_id(context, patient_id, task_key)
        storage = Path(os.getenv("AGENT_STORAGE_PATH", "./storage"))
        checkpoint_database = storage / "patient-checkpoints.sqlite3"
        storage.mkdir(parents=True, exist_ok=True)

        from deepagents import (
            GeneralPurposeSubagentProfile,
            HarnessProfile,
            create_deep_agent,
            register_harness_profile,
        )
        from langchain.chat_models import init_chat_model
        from langchain.mcp import MCPAdapter
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.types import Command

        # DeepAgents 继续负责编排；只有 SSE 请求显式打开 LangChain 模型流式输出。
        streaming_model = init_chat_model(model, streaming=True) if on_delta is not None else model

        # 非患者角色仍然不带 task 工具；只有这里显式配置子 Agent 的图会启用委派。
        register_harness_profile(
            "openai",
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
                excluded_tools={"delete", "edit_file", "execute", "glob", "grep", "ls", "read_file", "write_file", "write_todos"},
            ),
        )

        memory = self.memory_store
        memory_enabled = self._memory_enabled(context, message)
        if not memory_enabled:
            await asyncio.to_thread(memory.delete_patient, patient_id)
        recalled = await asyncio.to_thread(memory.search, patient_id, message, 4) if memory_enabled else []
        memory_tool = lambda query: memory.search(patient_id, query, limit=5)
        remember = lambda content: memory.remember(patient_id, content, thread_id)

        async with MCPAdapter(mcp_transport()) as adapter:
            available = {item.name: item for item in await adapter.list_tools()}
            if self._is_generic_booking_request(message):
                appointment_tool = available.get("search_appointments")
                if appointment_tool is not None:
                    result = await appointment_tool.ainvoke({"patient_id": patient_id})
                    existing_appointment = self._existing_clinic_appointment(result)
                    if existing_appointment:
                        if memory_enabled:
                            await asyncio.to_thread(memory.archive, patient_id, thread_id, task_key, "user", message)
                            await asyncio.to_thread(memory.archive, patient_id, thread_id, task_key, "assistant", existing_appointment)
                        if on_delta is not None:
                            on_delta(existing_appointment)
                        return existing_appointment

            registry = PatientAgentRegistry()
            subagents = registry.deepagent_specs(available, patient_id, task_key)
            tools = create_patient_tools(registry, memory_tool, remember, memory_enabled)
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint_database)) as checkpointer:
                await checkpointer.setup()
                graph = create_deep_agent(
                    model=streaming_model,
                    tools=tools,
                    system_prompt=self._prompt_with_context(recalled),
                    subagents=subagents,
                    checkpointer=checkpointer,
                )
                config = {"configurable": {"thread_id": thread_id}}
                if context.get("continueAfterInterrupt"):
                    from langchain_core.messages import HumanMessage

                    await graph.aupdate_state(config, {"messages": [HumanMessage(
                        content="（会话记录：用户已打断上一轮生成。请基于已有对话和当前消息继续处理。）"
                    )]})
                approval = context.get("approvePendingWrite")
                agui_resume = context.get("_agui_resume")
                if approval not in (None, True, False):
                    raise ValueError("approvePendingWrite 只能是 true 或 false。")
                if approval is not None or agui_resume is not None:
                    state = await graph.aget_state(config)
                    interrupts = [
                        interrupt
                        for task in state.tasks
                        for interrupt in task.interrupts
                    ]
                    if not interrupts:
                        raise ValueError("当前对话没有等待确认的写入。")
                    if agui_resume is not None:
                        if not isinstance(agui_resume, list):
                            raise ValueError("AG-UI resume 必须是数组。")
                        decisions = self._agui_interrupt_decisions(interrupts, agui_resume)
                    else:
                        decision = {"type": "approve" if approval else "reject"}
                        if not approval:
                            decision["message"] = "患者没有同意这次写入。"
                        decisions = {interrupt.id: decision for interrupt in interrupts}
                    resumed = await graph.ainvoke(
                        Command(resume=decisions),
                        config=config,
                    )
                    if self._has_pending_interrupt(resumed) and on_interrupt is not None:
                        on_interrupt(self._agui_interrupts(resumed))
                    reply = self._last_reply(resumed)
                    if on_delta is not None:
                        on_delta(reply)
                    if memory_enabled:
                        await asyncio.to_thread(memory.archive, patient_id, thread_id, task_key, "assistant", reply)
                    return reply

                prompt = message.strip()
                if context:
                    prompt += "\n\n当前请求提供的业务上下文：\n" + json.dumps(
                        self._public_context(context), ensure_ascii=False
                    )
                request_state = {"messages": [{"role": "user", "content": prompt}]}
                if on_delta is None:
                    result = await graph.ainvoke(request_state, config=config)
                else:
                    streamed_text = False
                    async for event in graph.astream_events(request_state, config=config, version="v2"):
                        metadata = event.get("metadata", {})
                        namespace = metadata.get("langgraph_checkpoint_ns", "")
                        if (
                            event.get("event") == "on_chat_model_stream"
                            and (not namespace or "|" not in namespace)
                        ):
                            chunk = event.get("data", {}).get("chunk")
                            content = getattr(chunk, "content", "")
                            if isinstance(content, str) and content:
                                streamed_text = True
                                on_delta(content)
                    state = await graph.aget_state(config)
                    result = dict(state.values)
                    interrupts = [interrupt for task in state.tasks for interrupt in task.interrupts]
                    if interrupts:
                        result["__interrupt__"] = interrupts
                reply = self._approval_summary(result) if self._has_pending_interrupt(result) else self._last_reply(result)
                if self._has_pending_interrupt(result) and on_interrupt is not None:
                    on_interrupt(self._agui_interrupts(result))
                if on_delta is not None and not streamed_text:
                    on_delta(reply)
                if memory_enabled:
                    await asyncio.to_thread(memory.archive, patient_id, thread_id, task_key, "user", message)
                    await asyncio.to_thread(memory.archive, patient_id, thread_id, task_key, "assistant", reply)
                return reply

    @staticmethod
    def _thread_id(context: dict[str, Any], patient_id: str, task_key: str) -> str:
        supplied = context.get("conversationId")
        if supplied is None:
            return f"patient:{patient_id}:{task_key}"
        if not isinstance(supplied, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", supplied):
            raise ValueError("context.conversationId 只能包含字母、数字、下划线、点、冒号和短横线，长度不超过 128。")
        return f"patient:{patient_id}:{supplied}"

    @staticmethod
    def _public_context(context: dict[str, Any]) -> dict[str, Any]:
        private_keys = {"approvePendingWrite", "memoryConsent", "memoryPreference", "conversationId", "_task_key", "continueAfterInterrupt", "_agui_resume"}
        return {key: value for key, value in context.items() if key not in private_keys}

    @staticmethod
    def _prompt_with_context(recalled: list[dict[str, str]]) -> str:
        text = PATIENT_PROMPT
        if recalled:
            text += "\n\n以下是同一合成患者此前对话的语义检索片段，只作上下文参考；与当前请求冲突时优先按当前请求处理：\n"
            text += json.dumps(recalled, ensure_ascii=False)
        return text

    @staticmethod
    def _memory_enabled(context: dict[str, Any], message: str = "") -> bool:
        """默认为当前患者召回并归档；明确关闭时清除该演示患者的记忆。"""
        if context.get("memoryConsent") is False:
            return False
        stop_phrases = ("不再保存", "不要保存", "别保存", "忘记我", "删除记忆", "清除记忆", "stop remembering", "forget me")
        return not any(phrase in message.lower() for phrase in stop_phrases)

    @cached_property
    def memory_store(self) -> PatientMemoryVectorStore:
        storage = Path(os.getenv("AGENT_STORAGE_PATH", "./storage"))
        model_dir = Path(os.getenv(
            "AGENT_MODEL_DIR",
            str(Path(__file__).resolve().parents[3] / ".." / "knowledge-platform" / "models"),
        ))
        return PatientMemoryVectorStore(storage / "patient-memory-vectors.sqlite3", model_dir)

    @staticmethod
    def _has_pending_interrupt(result: Any) -> bool:
        interrupts = getattr(result, "interrupts", ())
        return bool(interrupts) or bool(result.get("__interrupt__"))

    @staticmethod
    def _agui_interrupt_decisions(interrupts: list[Any], responses: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
        response_by_id = {item.get("interrupt_id"): item for item in responses}
        expected = {item.id for item in interrupts}
        if set(response_by_id) != expected:
            raise ValueError("AG-UI resume 必须一次回应当前会话的全部待处理确认。")
        decisions: dict[str, dict[str, str]] = {}
        for interrupt_id, item in response_by_id.items():
            if item.get("status") == "cancelled":
                decisions[interrupt_id] = {"type": "reject", "message": "患者取消了这次写入。"}
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict) or not isinstance(payload.get("approved"), bool):
                raise ValueError("AG-UI 确认内容需要提供布尔值 approved。")
            decision = {"type": "approve" if payload["approved"] else "reject"}
            if not payload["approved"]:
                decision["message"] = "患者没有同意这次写入。"
            decisions[interrupt_id] = decision
        return decisions

    @staticmethod
    def _agui_interrupts(result: dict[str, Any]) -> list[dict[str, Any]]:
        pending = result.get("__interrupt__", [])
        return [
            {
                "id": item.id,
                "reason": "confirmation",
                "message": PatientMainAgent._approval_summary(result),
                "responseSchema": {
                    "type": "object",
                    "properties": {"approved": {"type": "boolean"}},
                    "required": ["approved"],
                },
            }
            for item in pending
        ]

    @staticmethod
    def _approval_summary(result: Any) -> str:
        """把暂停的写入内容给患者看清楚，再让患者决定是否继续。"""
        labels = {
            "create_appointment": "创建预约",
            "create_service_case": "提交服务核实申请",
            "create_task": "创建服务跟进记录",
            "create_service_request_draft": "创建检查申请草稿",
        }
        interrupts = getattr(result, "interrupts", ())
        if not interrupts:
            interrupts = result.get("__interrupt__", ())
        details: list[str] = []
        for interrupt in interrupts:
            value = getattr(interrupt, "value", interrupt)
            actions = value.get("action_requests", []) if isinstance(value, dict) else []
            if isinstance(value, dict) and value.get("name"):
                actions = [{"name": value["name"], "args": value.get("args", {})}]
            for action in actions:
                label = labels.get(action.get("name"), "写入演示资料")
                args = json.dumps(action.get("args", {}), ensure_ascii=False)
                details.append(f"- {label}：{args}")
        if not details:
            details = ["- 写入或更新演示资料（具体内容请检查调用方展示）"]
        return "准备执行以下操作：\n" + "\n".join(details) + "\n\n确认后请在下一次请求里传 context.approvePendingWrite=true；取消请传 false。"

    @staticmethod
    def _last_reply(result: dict[str, Any]) -> str:
        for item in reversed(result.get("messages", [])):
            if getattr(item, "type", None) == "ai" or getattr(item, "role", None) == "assistant":
                content = getattr(item, "content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                if isinstance(content, list):
                    parts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
                    if parts:
                        return "\n".join(parts).strip()
        raise RuntimeError("患者 Agent 没有返回文字回复。")
