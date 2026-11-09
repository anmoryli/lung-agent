"""患者主 Agent：Gateway 内的唯一患者工作流入口。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from functools import cached_property
from typing import Any, Callable

from backend.agents.base import AgentConfigurationError, RoleMainAgent
from backend.agents.patient.registry import PatientAgentRegistry
from backend.agents.patient.tools import create_patient_tools
from backend.integrations.memory.vector_store import PatientMemoryVectorStore
from backend.integrations.mcp.patient_fhir import mcp_transport, patient_id_from_context
from backend.runtime.active_runs import active_runs
from backend.runtime.async_runner import run_async
from backend.runtime.clock import current_time_context


PATIENT_PROMPT = """
你是基于 DeepAgents 的患者服务助手。先识别患者当前要办的事，再决定直接回答还是交给一个最合适的专科助手。

工作顺序：
1. 只处理患者当前提出的事情。打招呼就简短回应，不罗列功能，也不主动输出无关提醒。
2. 患者说“挂号”“帮我挂号”或指定科室要求挂号时，一律按新挂号申请处理。不要先查询或展示已有预约；只有患者明确询问已有预约、预约时间或预约状态时，才查询已有预约。
3. 需要病历事实时，只让专科助手查询当前合成患者资料。资料里没有的内容就说明暂时查不到。
4. 先使用当前会话、患者资料和相关历史补齐信息，不让患者重复提供系统已有的信息。缺少会阻塞下一步的内容时，每轮只问一个最关键的问题；能安全继续时直接继续，不连续追问。
5. 一次只委派一个专科助手，不让子助手互相委派。只展示与当前请求有关的信息，不附带其他科室、护理或检查安排。
6. 写入工具会触发确认。调用前用一句话说明要提交的内容，然后等待确认卡；不要额外再问一次口头同意。患者已明确答应本轮提出的具体写入申请时，直接调用写入工具。预约申请提交后是“待医院确认”，不能说成已经挂号成功。系统没有号源查询能力时，不得声称查到号源或保证时间。
7. 回复简短、易懂，只给当前结论和下一步。医疗判断由医生确认；不诊断、不建议停换药、不决定检查必要性，不承诺资格、费用或服务可用。
8. 每轮系统提示词会附带当前日期时间和时区，回答“明天/下周/现在”等问题时以此为准；需要刷新时可调用 get_current_time。
9. 患者询问肺癌一般知识、术语或科普资料时，按需调用 search_cancer_knowledge。具体术语、检查或治疗问题用 local；询问整个知识库的主要主题或整体脉络时用 global。回答附上命中资料标题和原始链接；寒暄、预约、患者本人病历或报告查询不调用它。它不能替代医生判断，也不能用来推断患者个人病情。

患者提到严重呼吸困难、胸痛、咯血、高热寒战、严重腹泻或自伤想法时，优先建议立即联系当地急救或就近急诊。不要把高风险情况说成在家观察即可，也不要输出内部风险标签、工具名、Agent 名或推理过程。

默认从当前患者自己的对话归档中按语义召回相关内容，并归档新的对话。只允许在同一合成患者编号下检索；患者明确要求不再保存时停止归档并清除该患者的向量记忆。

记忆默认可读取，不再询问授权。每轮都要把系统维护的短期患者记忆摘要纳入模型上下文；对话归档仍单独做向量检索，不能把旧归档直接当成当前事实。患者明确要求“记住”时，优先立即写入；用户原话里的稳定事实也会在回合结束后由后台抽取。过敏、长期用药和禁忌必须标为患者自述并保留原话，不能当作已核实诊断；临时症状、报告内容和助手推断不进入长期记忆。
患者明确纠正旧偏好时，用 supersedes_id 替代旧条目；来源互相矛盾且无法确认新旧时，用 conflicts_with_id 标成冲突，双方都不放入每轮摘要。
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

    async def _invoke_patient(
        self,
        message: str,
        context: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
        on_interrupt: Callable[[list[dict[str, Any]]], None] | None = None,
        on_state: Callable[[dict[str, Any]], None] | None = None,
        on_activity: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        current_todos: list[dict[str, str]] = []
        current_activity = "正在准备本轮处理"
        model_context = {
            "model_id": context.get("_model_id", ""),
            "model_name": context.get("_model_name", ""),
            "context_window": context.get("_model_context_window", 0),
            "input_budget_tokens": context.get("_model_input_budget", 0),
            "summarization_trigger_tokens": round(context.get("_model_input_budget", 0) * 0.85),
            "max_output_tokens": context.get("_model_max_output_tokens", 0),
            "estimated_input_tokens": 0,
        }

        def update_state(activity: str, todos: list[dict[str, str]] | None = None) -> None:
            nonlocal current_activity, current_todos
            current_activity = activity
            if todos is not None:
                current_todos = todos
            if on_state is not None:
                on_state({"activity": current_activity, "todos": current_todos, "model": model_context})

        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise AgentConfigurationError("请先配置 OPENAI_API_KEY。")
        model_id = str(context.get("_model_id") or os.getenv("DEEPAGENTS_MODEL", "openai:deepseek-flash").split(":", 1)[-1]).strip()
        model_name = f"openai:{model_id}"
        context_window = context.get("_model_context_window")
        input_budget = context.get("_model_input_budget")
        max_output_tokens = context.get("_model_max_output_tokens")
        if (
            not isinstance(context_window, int)
            or not isinstance(input_budget, int)
            or not isinstance(max_output_tokens, int)
        ):
            raise AgentConfigurationError("当前模型没有可验证的上下文窗口资料。")

        patient_id = patient_id_from_context(context)
        time_context = current_time_context()
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

        # 总上下文窗口包含输入和输出；给摘要中间件的输入预算先预留最大输出空间。
        streaming_model = init_chat_model(model_name, streaming=on_delta is not None)
        streaming_model.profile = {
            **(streaming_model.profile or {}),
            "max_input_tokens": input_budget,
            "max_output_tokens": max_output_tokens,
        }

        # 非患者角色仍然不带 task 工具；只有这里显式配置子 Agent 的图会启用委派。
        register_harness_profile(
            "openai",
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
                excluded_tools={"delete", "edit_file", "execute", "glob", "grep", "ls", "read_file", "write_file"},
            ),
        )

        memory = self.memory_store
        memory_requested = self._memory_enabled(context, message)
        if not memory_requested:
            active_runs.request_patient_deletion(patient_id)
            try:
                await asyncio.to_thread(memory.request_patient_deletion, patient_id)
            except Exception:
                active_runs.cancel_patient_deletion_request(patient_id)
                raise
            memory_enabled = False
        else:
            if context.get("memoryConsent") is True:
                await asyncio.to_thread(memory.set_patient_memory_enabled, patient_id, True)
            memory_enabled = await asyncio.to_thread(memory.patient_memory_enabled, patient_id)
        if not memory_enabled:
            long_term_profile = {"summary": "暂无已确认的长期偏好或服务安排。"}
        elif on_state is not None:
            update_state("正在查找相关历史记忆")
        if memory_enabled:
            long_term_profile = await asyncio.to_thread(memory.build_long_term_profile, patient_id)
        recalled = await asyncio.to_thread(
            memory.search, patient_id, message, 4, thread_id
        ) if memory_enabled else []
        memory_tool = lambda query: memory.search(patient_id, query, limit=5, conversation_id=thread_id)
        remember = lambda content, category, importance, expires_in_days, supersedes_id, evidence, conflicts_with_id: memory.remember(
            patient_id, content, thread_id, category, importance, expires_in_days,
            supersedes_id, evidence, conflicts_with_id,
        )

        update_state("正在连接患者资料")
        async with MCPAdapter(mcp_transport()) as adapter:
            available = {item.name: item for item in await adapter.list_tools()}
            registry = PatientAgentRegistry()
            subagents = registry.deepagent_specs(
                available, patient_id, task_key, long_term_profile["summary"], time_context
            )
            tools = create_patient_tools(registry, memory_tool, remember, memory_enabled)
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint_database)) as checkpointer:
                await checkpointer.setup()
                graph = create_deep_agent(
                    model=streaming_model,
                    tools=tools,
                    system_prompt=self._prompt_with_context(
                        recalled, long_term_profile["summary"], time_context
                    ),
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
                    update_state("正在恢复待确认操作")
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
                        decisions = {
                            interrupt.id: {"decisions": [decision]}
                            for interrupt in interrupts
                        }
                    resumed = await graph.ainvoke(
                        Command(resume=decisions),
                        config=config,
                    )
                    if self._has_pending_interrupt(resumed) and on_interrupt is not None:
                        update_state("等待你确认", current_todos)
                        on_interrupt(self._agui_interrupts(resumed))
                    reply = self._last_reply(resumed)
                    if on_delta is not None:
                        on_delta(reply)
                    if memory_enabled:
                        await asyncio.to_thread(memory.archive, patient_id, thread_id, task_key, "assistant", reply)
                    if not self._has_pending_interrupt(resumed):
                        update_state("已完成", current_todos)
                    return reply

                prompt = message.strip()
                if context:
                    prompt += "\n\n当前请求提供的业务上下文：\n" + json.dumps(
                        self._public_context(context), ensure_ascii=False
                    )
                request_state = {"messages": [{"role": "user", "content": prompt}]}
                update_state("正在理解你的问题")
                if on_delta is None:
                    result = await graph.ainvoke(request_state, config=config)
                else:
                    streamed_text = False
                    model_runs: dict[str, dict[str, Any]] = {}
                    active_subagent_name = ""

                    def set_trace(run_id: str, namespace: str, status: str, *, first_output: bool = False) -> None:
                        if on_activity is None:
                            return
                        agent_name = self._trace_agent_name(namespace, active_subagent_name)
                        details = model_runs.setdefault(
                            run_id, {"first_output_ms": None, "started_at": time.perf_counter()}
                        )
                        if first_output and details["first_output_ms"] is None:
                            details["first_output_ms"] = round(
                                (time.perf_counter() - details["started_at"]) * 1000
                            )
                        on_activity({
                            "id": run_id,
                            "agent": agent_name,
                            "model": context.get("_model_name", model_name),
                            "status": status,
                            "first_output_ms": details["first_output_ms"],
                            "updated_at": time.time(),
                        })

                    async for event in graph.astream_events(request_state, config=config, version="v2"):
                        metadata = event.get("metadata", {})
                        namespace = metadata.get("langgraph_checkpoint_ns", "")
                        event_name = event.get("event")
                        event_run_id = str(event.get("run_id") or f"{event_name}-{time.time_ns()}")
                        if not namespace or "|" not in namespace:
                            if event_name == "on_chat_model_start":
                                update_state("正在理解和整理答复")
                                estimated = self._estimate_model_input_tokens(event.get("data", {}).get("input"))
                                if estimated:
                                    model_context["estimated_input_tokens"] = estimated
                                    update_state(current_activity, current_todos)
                                model_runs[event_run_id] = {
                                    "first_output_ms": None,
                                    "started_at": time.perf_counter(),
                                }
                                set_trace(event_run_id, namespace, "正在调用主模型")
                            elif event_name == "on_tool_start":
                                tool_name = event.get("name", "")
                                tool_input = event.get("data", {}).get("input", {})
                                if tool_name == "task" and isinstance(tool_input, dict):
                                    active_subagent_name = str(tool_input.get("subagent_type", ""))
                                if tool_name == "write_todos":
                                    current_todos = self._agui_todos_from_tool_input(tool_input)
                                    update_state("正在更新处理计划", current_todos)
                                else:
                                    update_state(self._agui_activity_for_tool(tool_name))
                                set_trace(event_run_id, namespace, f"正在运行 {tool_name}")
                            elif event_name == "on_tool_end":
                                set_trace(event_run_id, namespace, f"{event.get('name', '工具')} 已完成")
                                if event.get("name") == "task":
                                    active_subagent_name = ""
                            elif event_name in {"on_chat_model_end", "on_chain_end"}:
                                if event_run_id in model_runs:
                                    if model_runs[event_run_id]["first_output_ms"] is None:
                                        set_trace(event_run_id, namespace, "模型已完成（服务未提供流式首 token）")
                                    else:
                                        set_trace(event_run_id, namespace, "模型本次生成完成")
                        elif event_name == "on_chat_model_start":
                            model_runs[event_run_id] = {
                                "first_output_ms": None,
                                "started_at": time.perf_counter(),
                            }
                            set_trace(event_run_id, namespace, "专科 Agent 正在调用模型")
                        elif event_name == "on_tool_start":
                            set_trace(event_run_id, namespace, f"专科 Agent 正在运行 {event.get('name', '工具')}")
                        elif event_name == "on_tool_end":
                            set_trace(event_run_id, namespace, f"{event.get('name', '工具')} 已完成")
                        elif event_name in {"on_chat_model_end", "on_chain_end"}:
                            if event_run_id in model_runs:
                                if model_runs[event_run_id]["first_output_ms"] is None:
                                    set_trace(event_run_id, namespace, "模型已完成（服务未提供流式首 token）")
                                else:
                                    set_trace(event_run_id, namespace, "模型本次生成完成")
                        if (
                            event_name == "on_chat_model_stream"
                        ):
                            chunk = event.get("data", {}).get("chunk")
                            if event_run_id not in model_runs:
                                set_trace(event_run_id, namespace, "模型开始返回首个流式片段", first_output=True)
                            elif model_runs[event_run_id]["first_output_ms"] is None:
                                set_trace(event_run_id, namespace, "模型开始返回首个流式片段", first_output=True)
                            content = getattr(chunk, "content", "")
                            if not namespace or "|" not in namespace:
                                if isinstance(content, str) and content:
                                    streamed_text = True
                                    on_delta(content)
                                elif isinstance(content, list):
                                    visible = "".join(
                                        str(part.get("text", "")) for part in content
                                        if isinstance(part, dict) and part.get("type") == "text"
                                    )
                                    if visible:
                                        streamed_text = True
                                        on_delta(visible)
                    state = await graph.aget_state(config)
                    result = dict(state.values)
                    interrupts = [interrupt for task in state.tasks for interrupt in task.interrupts]
                    if interrupts:
                        result["__interrupt__"] = interrupts
                reply = self._approval_summary(result) if self._has_pending_interrupt(result) else self._last_reply(result)
                if self._has_pending_interrupt(result) and on_interrupt is not None:
                    update_state("等待你确认", current_todos)
                    on_interrupt(self._agui_interrupts(result))
                if on_delta is not None and not streamed_text:
                    on_delta(reply)
                if memory_enabled:
                    user_entry_id = await asyncio.to_thread(
                        memory.archive, patient_id, thread_id, task_key, "user", message
                    )
                    if user_entry_id:
                        await asyncio.to_thread(
                            memory.enqueue_extraction, patient_id, thread_id, task_key, user_entry_id
                        )
                    await asyncio.to_thread(memory.archive, patient_id, thread_id, task_key, "assistant", reply)
                if not self._has_pending_interrupt(result):
                    update_state("已完成", current_todos)
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
    def _trace_agent_name(namespace: str, active_subagent_name: str = "") -> str:
        """从 LangGraph 子图命名空间里取专科 Agent 名，不把运行 ID 发给页面。"""
        if not namespace or "|" not in namespace:
            return "patient"
        match = re.search(r"(?:^|[|:])task:([^|:]+)", namespace)
        if match:
            return match.group(1).replace("_", " ")
        return active_subagent_name.replace("_", " ") if active_subagent_name else "patient"

    @staticmethod
    def _public_context(context: dict[str, Any]) -> dict[str, Any]:
        private_keys = {
            "approvePendingWrite", "memoryConsent", "memoryPreference", "conversationId",
            "_task_key", "continueAfterInterrupt", "_agui_resume", "_model_id",
            "_model_name", "_model_context_window", "_model_max_output_tokens",
            "_model_input_budget",
        }
        return {key: value for key, value in context.items() if key not in private_keys}

    @staticmethod
    def _prompt_with_context(
        recalled: list[dict[str, str]], long_term_profile: str = "", time_context: str = ""
    ) -> str:
        text = PATIENT_PROMPT
        if time_context:
            text += "\n\n本轮时间基准（服务自动获取）：\n" + time_context
        text += "\n\n当前患者长期记忆摘要（每轮更新，过期和低权重记忆不放入此处）：\n" + (
            long_term_profile or "暂无已确认的长期偏好或服务安排。"
        )
        if recalled:
            text += "\n\n以下是同一合成患者此前对话的语义检索片段，只作上下文参考；与当前请求冲突时优先按当前请求处理：\n"
            text += json.dumps(recalled, ensure_ascii=False)
        return text

    @staticmethod
    def _estimate_model_input_tokens(model_input: Any) -> int:
        """从 LangChain 即将送出的消息估算输入占用，供前端显示趋势。"""
        if model_input is None:
            return 0
        from langchain.agents.middleware.summarization import count_tokens_approximately

        messages = model_input.get("messages") if isinstance(model_input, dict) else model_input
        if not isinstance(messages, (list, tuple)):
            return 0
        # ChatModel 的回调可能把单次调用包成一层 batch 列表。
        if len(messages) == 1 and isinstance(messages[0], list):
            messages = messages[0]
        tools = model_input.get("tools") if isinstance(model_input, dict) else None
        try:
            return count_tokens_approximately(messages, tools=tools)
        except (TypeError, ValueError, NotImplementedError):
            logging.warning("无法估算本轮模型输入 token，继续运行并在页面显示窗口上限。")
            return 0

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
    def _agui_interrupt_decisions(interrupts: list[Any], responses: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, str]]]]:
        response_by_id = {item.get("interrupt_id"): item for item in responses}
        expected = {item.id for item in interrupts}
        if set(response_by_id) != expected:
            raise ValueError("AG-UI resume 必须一次回应当前会话的全部待处理确认。")
        decisions: dict[str, dict[str, list[dict[str, str]]]] = {}
        for interrupt_id, item in response_by_id.items():
            if item.get("status") == "cancelled":
                decisions[interrupt_id] = {"decisions": [{"type": "reject", "message": "患者取消了这次写入。"}]}
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict) or not isinstance(payload.get("approved"), bool):
                raise ValueError("AG-UI 确认内容需要提供布尔值 approved。")
            decision = {"type": "approve" if payload["approved"] else "reject"}
            if not payload["approved"]:
                decision["message"] = "患者没有同意这次写入。"
            decisions[interrupt_id] = {"decisions": [decision]}
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
    def _agui_todos_from_tool_input(tool_input: Any) -> list[dict[str, str]]:
        """只把计划文字和进度状态发给页面，不转发工具的其它参数。"""
        if not isinstance(tool_input, dict) or not isinstance(tool_input.get("todos"), list):
            return []
        todos = []
        for item in tool_input["todos"]:
            if not isinstance(item, dict) or not isinstance(item.get("content"), str):
                continue
            status = item.get("status")
            if status not in ("pending", "in_progress", "completed"):
                status = "pending"
            todos.append({"content": item["content"].strip()[:180], "status": status})
        return todos[:12]

    @staticmethod
    def _agui_activity_for_tool(tool_name: str) -> str:
        """把内部工具调用映射成患者看得懂的简短状态。"""
        if tool_name == "task":
            return "正在处理相关事项"
        if tool_name == "discover_patient_agents":
            return "正在查找合适的专科助手"
        if tool_name == "search_patient_history":
            return "正在查找之前的对话"
        if tool_name == "save_patient_memory":
            return "正在保存你要求记住的信息"
        if tool_name.startswith("search_") or tool_name.startswith("get_"):
            return "正在查阅患者资料"
        if tool_name.startswith("create_") or tool_name.startswith("update_"):
            return "正在准备一项待确认的操作"
        return "正在处理当前事项"

    @staticmethod
    def _approval_summary(result: Any) -> str:
        """把暂停的写入内容整理成患者看得懂的确认卡文字。"""
        labels = {
            "create_appointment": "提交挂号申请",
            "create_service_case": "提交服务申请",
            "create_task": "创建服务跟进任务",
            "create_service_request_draft": "创建服务申请草稿",
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
                name = action.get("name", "")
                args = action.get("args", {})
                if name == "create_appointment":
                    start = str(args.get("start", "待安排")).replace("T", " ")[:16]
                    end = str(args.get("end", "")).replace("T", " ")[:16]
                    when = f"{start} 至 {end}" if end else start
                    description = str(args.get("description", "就诊预约申请"))
                    details.append(
                        f"- {labels[name]}：{description}；申请时间：{when}；提交后状态为待医院确认。"
                    )
                else:
                    details.append(
                        f"- {labels.get(name, '提交写入申请')}："
                        + json.dumps(args, ensure_ascii=False)
                    )
        if not details:
            details = ["- 写入申请内容暂时无法读取，请联系服务维护人员。"]
        return "请核对这项操作：\n" + "\n".join(details)

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
