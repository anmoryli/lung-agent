"""患者主 Agent：Gateway 内的唯一患者工作流入口。"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Any

from backend.agents.base import AgentConfigurationError, RoleMainAgent
from backend.agents.patient.subagents import PATIENT_SPECIALISTS
from backend.integrations.patient_mcp import WRITE_TOOLS, make_patient_tool, mcp_transport, patient_id_from_context
from backend.runtime.async_runner import run_async


PATIENT_PROMPT = """
你是肺癌患者服务的主 Agent，Gateway 会把患者请求交给你。先判断患者现在的目标，再决定自己回答还是交给一个最合适的专科助手。

工作顺序：
1. 识别目标：症状安全、报告、检查或预约、问诊准备、心理支持、医保费用、交通陪同、上门护理。打招呼或一般问题直接回答。
2. 需要病历事实时，只让专科助手查当前合成患者的资料。资料里没有的内容就说暂时查不到。
3. 缺少关键信息时，每轮最多问两个问题。一次只委派一个专科助手，不让子助手互相委派。
4. 任何写入、预约、申请或同步前，先把具体内容和影响告诉患者并取得明确同意。写工具还会暂停等待确认；没有确认就不执行。
5. 输出简短、易懂的当前结论和下一步。医疗判断由医生确认；不诊断、不建议停换药、不决定检查必要性，不承诺资格、费用或服务可用。

严重呼吸困难、胸痛、咯血、高热寒战、严重腹泻或自伤想法出现时，优先建议立即联系当地急救或就近急诊。不要把高风险情况说成在家观察即可，也不要输出内部风险标签、工具名、Agent 名或推理过程。

记忆只用于患者主动选择保存的沟通偏好；不把症状、报告、诊断、用药或家庭信息保存为长期记忆。患者服务当前只处理合成演示数据。
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

    async def _invoke_patient(self, message: str, context: dict[str, Any]) -> str:
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise AgentConfigurationError("请先配置 OPENAI_API_KEY。")
        model = os.getenv("DEEPAGENTS_MODEL", "openai:gpt-4.1-mini").strip()
        if not model.startswith("openai:"):
            raise AgentConfigurationError("当前只接入 OpenAI 兼容模型，请使用 openai:模型名 格式。")

        patient_id = patient_id_from_context(context)
        task_key = str(context.get("_task_key", "patient-task"))[:80]
        thread_id = self._thread_id(context, patient_id, task_key)
        storage = Path(os.getenv("AGENT_STORAGE_PATH", "./storage"))
        memory_database = storage / "patient-memory.sqlite3"
        checkpoint_database = storage / "patient-checkpoints.sqlite3"
        storage.mkdir(parents=True, exist_ok=True)

        from deepagents import (
            GeneralPurposeSubagentProfile,
            HarnessProfile,
            create_deep_agent,
            register_harness_profile,
        )
        from langchain.mcp import MCPAdapter
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.store.sqlite import SqliteStore
        from langgraph.types import Command

        # 非患者角色仍然不带 task 工具；只有这里显式配置子 Agent 的图会启用委派。
        register_harness_profile(
            "openai",
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
                excluded_tools={"delete", "edit_file", "execute", "glob", "grep", "ls", "read_file", "write_file", "write_todos"},
            ),
        )

        with self._sqlite_connection(memory_database) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            store = SqliteStore(connection)
            store.setup()
            await self._save_explicit_preference(store, patient_id, context)
            preference = self._load_preference(store, patient_id, context)

            async with MCPAdapter(mcp_transport()) as adapter:
                available = {tool.name: tool for tool in await adapter.list_tools()}
                subagents, _ = self._build_subagents(
                    available, patient_id, task_key
                )
                async with AsyncSqliteSaver.from_conn_string(str(checkpoint_database)) as checkpointer:
                    await checkpointer.setup()
                    graph = create_deep_agent(
                        model=model,
                        tools=[],
                        system_prompt=self._prompt_with_context(context, preference),
                        subagents=subagents,
                        checkpointer=checkpointer,
                        store=store,
                    )
                    config = {"configurable": {"thread_id": thread_id}}
                    approval = context.get("approvePendingWrite")
                    if approval not in (None, True, False):
                        raise ValueError("approvePendingWrite 只能是 true 或 false。")
                    if approval is not None:
                        state = await graph.aget_state(config)
                        interrupts = [
                            interrupt
                            for task in state.tasks
                            for interrupt in task.interrupts
                        ]
                        if not interrupts:
                            raise ValueError("当前对话没有等待确认的写入。")
                        decision = {"type": "approve" if approval else "reject"}
                        if not approval:
                            decision["message"] = "患者没有同意这次写入。"
                        resumed = await graph.ainvoke(
                            Command(resume={interrupt.id: decision for interrupt in interrupts}),
                            config=config,
                        )
                        return self._last_reply(resumed)

                    prompt = message.strip()
                    if context:
                        prompt += "\n\n当前请求提供的业务上下文：\n" + json.dumps(
                            self._public_context(context), ensure_ascii=False
                        )
                    result = await graph.ainvoke(
                        {"messages": [{"role": "user", "content": prompt}]}, config=config
                    )
                    if self._has_pending_interrupt(result):
                        return self._approval_summary(result)
                    return self._last_reply(result)

    @staticmethod
    def _thread_id(context: dict[str, Any], patient_id: str, task_key: str) -> str:
        supplied = context.get("conversationId")
        if supplied is None:
            return f"patient:{patient_id}:{task_key}"
        if not isinstance(supplied, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", supplied):
            raise ValueError("context.conversationId 只能包含字母、数字、下划线和短横线，长度不超过 96。")
        return f"patient:{patient_id}:{supplied}"

    @staticmethod
    @contextmanager
    def _sqlite_connection(database: Path):
        """每个请求独立开连接，线程间不共用 SQLite 连接。"""
        connection = sqlite3.connect(database, timeout=30, check_same_thread=False)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _public_context(context: dict[str, Any]) -> dict[str, Any]:
        private_keys = {"approvePendingWrite", "memoryConsent", "memoryPreference", "conversationId", "_task_key"}
        return {key: value for key, value in context.items() if key not in private_keys}

    @staticmethod
    def _prompt_with_context(context: dict[str, Any], preference: dict[str, str] | None) -> str:
        text = PATIENT_PROMPT
        if preference:
            text += "\n\n患者同意保存的沟通偏好（仅用于表达方式）：" + json.dumps(preference, ensure_ascii=False)
        return text

    @staticmethod
    async def _save_explicit_preference(store: Any, patient_id: str, context: dict[str, Any]) -> None:
        # 演示服务没有登录鉴权，只把已标记同意且白名单内的偏好写入合成患者命名空间。
        preference = context.get("memoryPreference")
        namespace = ("patient-demo", patient_id, "behavioral")
        if context.get("memoryConsent") is False:
            store.delete(namespace, "preferences")
            return
        if context.get("memoryConsent") is not True or not isinstance(preference, dict):
            return
        allowed = {"language", "communication_style", "accessibility"}
        clean = {
            key: value.strip()[:120]
            for key, value in preference.items()
            if key in allowed and isinstance(value, str) and value.strip()
        }
        if clean:
            store.put(namespace, "preferences", clean)

    @staticmethod
    def _load_preference(store: Any, patient_id: str, context: dict[str, Any]) -> dict[str, str] | None:
        if context.get("memoryConsent") is not True:
            return None
        item = store.get(("patient-demo", patient_id, "behavioral"), "preferences")
        return item.value if item else None

    @staticmethod
    def _build_subagents(available: dict[str, Any], patient_id: str, task_key: str) -> tuple[list[dict[str, Any]], dict[str, bool]]:
        children: list[dict[str, Any]] = []
        pending: dict[str, bool] = {}
        for specialist in PATIENT_SPECIALISTS:
            required = specialist.read_tools + specialist.write_tools
            missing = [name for name in required if name not in available]
            if missing:
                raise AgentConfigurationError(
                    "FHIR MCP 服务缺少患者流程需要的工具：" + ", ".join(missing)
                )
            tools = [
                make_patient_tool(available[name], patient_id, task_key)
                for name in required
            ]
            child_pending = {tool.name: {"allowed_decisions": ["approve", "reject"]} for tool in tools if tool.name in WRITE_TOOLS}
            pending.update({name: True for name in child_pending})
            children.append({
                "name": specialist.name,
                "description": specialist.description,
                "system_prompt": specialist.system_prompt,
                "tools": tools,
                "interrupt_on": child_pending,
                "mode": "isolated",
            })
        return children, pending

    @staticmethod
    def _has_pending_interrupt(result: Any) -> bool:
        interrupts = getattr(result, "interrupts", ())
        return bool(interrupts) or bool(result.get("__interrupt__"))

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
