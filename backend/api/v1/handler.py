"""第一版 HTTP 路由和请求校验。"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ag_ui.core import (
    BaseEvent,
    EventType,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder
from pydantic import Field

from backend.agents.base import AgentConfigurationError
from backend.gateway.orchestration.gateway import Gateway, UnknownRoleError
from backend.shared.tasking.idempotency_store import IdempotencyConflict
from backend.runtime.active_runs import active_runs
from backend.runtime.async_runner import submit_async


gateway = Gateway()


class InterruptAwareRunFinishedEvent(RunFinishedEvent):
    """AG-UI 新版中断结束字段。"""

    outcome: dict = Field(default_factory=lambda: {"type": "success"})


class AgentRequestHandler(BaseHTTPRequestHandler):
    server_version = "AgentService/0.1"

    def do_GET(self) -> None:
        request_path = urlparse(self.path).path
        static_files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        if request_path in static_files:
            filename, content_type = static_files[request_path]
            page = Path(__file__).resolve().parents[3] / "frontend" / filename
            body = page.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if request_path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "找不到这个地址。"})

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        match = re.fullmatch(r"/api/v1/agents/patient/runs/([A-Za-z0-9_.:-]{1,128})", parsed.path)
        thread_id = parse_qs(parsed.query).get("threadId", [""])[0]
        if match is None or not thread_id:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "找不到这个取消地址。"})
            return
        if not active_runs.cancel(match.group(1), thread_id):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "找不到这个运行中的会话。"})
            return
        self._send_json(HTTPStatus.ACCEPTED, {"status": "cancelled", "threadId": thread_id})

    def do_OPTIONS(self) -> None:
        if urlparse(self.path).path != "/api/v1/agents/patient/ag-ui" and not urlparse(self.path).path.startswith(
            "/api/v1/agents/patient/runs/"
        ):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "找不到这个地址。"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_POST(self) -> None:
        request_path = urlparse(self.path).path
        if request_path == "/api/v1/agents/patient/ag-ui":
            self._handle_agui()
            return
        path = request_path.strip("/").split("/")
        if len(path) != 5 or path[:3] != ["api", "v1", "agents"] or path[4] != "invoke":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "找不到这个地址。"})
            return

        try:
            request_data = self._read_request()
            role = path[3]
            task_key = request_data.get("taskKey")
            message = request_data.get("message")
            context = request_data.get("context", {})
            if not isinstance(task_key, str) or not task_key.strip() or len(task_key) > 128:
                raise ValueError("taskKey 必须是 1 到 128 个字符。")
            if not isinstance(message, str) or not message.strip() or len(message) > 20000:
                raise ValueError("message 必须是 1 到 20000 个字符。")
            if not isinstance(context, dict):
                raise ValueError("context 必须是 JSON 对象。")

            reply, cached = gateway.invoke(role, task_key, message, context)
            self._send_json(
                HTTPStatus.OK,
                {"taskKey": task_key, "role": role, "reply": reply, "cached": cached},
            )
        except UnknownRoleError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "没有这个角色的主 Agent。"})
        except AgentConfigurationError as error:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})
        except IdempotencyConflict as error:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
        except (ValueError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except Exception:
            logging.exception("主 Agent 调用失败")
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                {"error": "主 Agent 调用失败，请检查模型配置和服务日志。"},
            )

    def _handle_agui(self) -> None:
        """按 AG-UI RunAgentInput 接收一轮对话，并用官方编码器输出 SSE。"""
        try:
            request_data = self._read_request()
            run_input = RunAgentInput.model_validate(request_data)
            message = self._last_user_message(run_input)
            if not message or len(message) > 20000:
                raise ValueError("AG-UI messages 需要包含一条不超过 20000 字的用户消息。")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", run_input.thread_id):
                raise ValueError("threadId 只能包含字母、数字、下划线、点、冒号和短横线，长度不超过 128。")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", run_input.run_id):
                raise ValueError("runId 只能包含字母、数字、下划线、点、冒号和短横线，长度不超过 128。")
            context = self._agui_context(run_input)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        try:
            agent = gateway.get_agent("patient")
            context["conversationId"] = run_input.thread_id
            context["_task_key"] = run_input.run_id
            if active_runs.take_interruption(run_input.thread_id):
                context["continueAfterInterrupt"] = True
            if run_input.resume is not None:
                context["_agui_resume"] = [
                    {
                        "interrupt_id": item.interrupt_id,
                        "status": item.status,
                        "payload": item.payload,
                    }
                    for item in run_input.resume
                ]
            events: queue.Queue = queue.Queue()
            push_state = os.getenv("AGENT_AGUI_PUSH_STATE", "true").strip().lower() in {"1", "true", "yes", "on"}
            if not active_runs.reserve(run_input.run_id, run_input.thread_id, events):
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {"error": "同一个 threadId 只能同时运行一个 Agent 请求，或 runId 已被占用。"},
                )
                return

            async def run_patient():
                def send_state(snapshot: dict) -> None:
                    if push_state:
                        events.put(("state", snapshot))

                if agent._needs_urgent_care(message):
                    send_state({"activity": "正在提供安全提醒", "todos": []})
                    reply = "你提到了需要尽快处理的情况。如果这些症状现在正在发生，或你担心自己会受伤，请马上联系当地急救或去最近的急诊；也请让身边可信任的人陪着你。"
                    events.put(("delta", reply))
                    send_state({"activity": "已完成", "todos": []})
                    return reply
                return await agent._invoke_patient(
                    message,
                    context,
                    lambda delta: events.put(("delta", delta)),
                    lambda interrupts: events.put(("interrupt", interrupts)),
                    send_state,
                )

            future = submit_async(run_patient())
            active_runs.attach(run_input.run_id, future)
            future.add_done_callback(lambda completed: self._queue_completion(events, completed))
        except Exception:
            active_runs.finish(run_input.run_id)
            logging.exception("AG-UI 患者运行初始化失败")
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": "患者 Agent 无法开始运行，请检查服务配置。"})
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._send_cors_headers()
        self.end_headers()
        encoder = EventEncoder()
        message_id = f"msg_{uuid.uuid4().hex}"
        streamed = False
        interrupted: list[dict] = []
        try:
            self._write_event(encoder, RunStartedEvent(
                type=EventType.RUN_STARTED,
                thread_id=run_input.thread_id,
                run_id=run_input.run_id,
            ))
            self._write_event(encoder, TextMessageStartEvent(
                type=EventType.TEXT_MESSAGE_START,
                message_id=message_id,
                role="assistant",
            ))
            while True:
                try:
                    kind, value = events.get(timeout=5)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                if kind == "delta" and value:
                    streamed = True
                    self._write_event(encoder, TextMessageContentEvent(
                        type=EventType.TEXT_MESSAGE_CONTENT,
                        message_id=message_id,
                        delta=value,
                    ))
                elif kind == "interrupt":
                    interrupted = value
                elif kind == "state":
                    self._write_event(encoder, StateSnapshotEvent(
                        type=EventType.STATE_SNAPSHOT,
                        snapshot=value,
                    ))
                elif kind == "done":
                    if not streamed and value:
                        self._write_event(encoder, TextMessageContentEvent(
                            type=EventType.TEXT_MESSAGE_CONTENT,
                            message_id=message_id,
                            delta=value,
                        ))
                    self._write_event(encoder, TextMessageEndEvent(
                        type=EventType.TEXT_MESSAGE_END,
                        message_id=message_id,
                    ))
                    finished = InterruptAwareRunFinishedEvent(
                        type=EventType.RUN_FINISHED,
                        thread_id=run_input.thread_id,
                        run_id=run_input.run_id,
                    )
                    if interrupted:
                        finished.outcome = {"type": "interrupt", "interrupts": interrupted}
                    self._write_event(encoder, finished)
                    break
                elif kind == "error":
                    if push_state:
                        self._write_event(encoder, StateSnapshotEvent(
                            type=EventType.STATE_SNAPSHOT,
                            snapshot={"activity": "本轮没有完成", "todos": []},
                        ))
                    self._write_event(encoder, RunErrorEvent(
                        type=EventType.RUN_ERROR,
                        message="患者 Agent 本轮运行失败，请查看服务日志。",
                        code="AGENT_RUN_FAILED",
                    ))
                    break
                elif kind == "cancelled":
                    break
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            active_runs.cancel(run_input.run_id, run_input.thread_id)
        finally:
            active_runs.finish(run_input.run_id)

    @staticmethod
    def _queue_completion(events: queue.Queue, future: object) -> None:
        try:
            result = future.result()
        except Exception as error:
            events.put(("error", error))
        else:
            events.put(("done", result))

    @staticmethod
    def _last_user_message(run_input: RunAgentInput) -> str:
        for message in reversed(run_input.messages):
            if message.role == "user":
                content = message.content
                if isinstance(content, str):
                    return content.strip()
                return "\n".join(
                    part.text for part in content
                    if getattr(part, "type", None) == "text" and getattr(part, "text", "").strip()
                ).strip()
        return ""

    @staticmethod
    def _agui_context(run_input: RunAgentInput) -> dict:
        allowed = {"patient_id", "memoryConsent", "approvePendingWrite"}
        context = {}
        for item in run_input.context:
            if item.description not in allowed:
                continue
            value = item.value
            if item.description in {"memoryConsent", "approvePendingWrite"}:
                if value.lower() in {"true", "false"}:
                    value = value.lower() == "true"
                else:
                    raise ValueError(f"AG-UI context {item.description} 必须是 true 或 false。")
            context[item.description] = value
        return context

    def _write_event(self, encoder: EventEncoder, event: BaseEvent) -> None:
        self.wfile.write(encoder.encode(event).encode("utf-8"))
        self.wfile.flush()

    def _read_request(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Content-Length 格式不正确。") from error
        if length < 1 or length > 1_000_000:
            raise ValueError("请求内容不能为空，且不能超过 1 MB。")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("请求内容必须是 JSON 对象。")
        return payload

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        allowed = [value.strip() for value in os.getenv("AGENT_CORS_ORIGINS", "*").split(",")]
        origin = self.headers.get("Origin", "")
        if "*" in allowed:
            self.send_header("Access-Control-Allow-Origin", "*")
        elif origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def log_message(self, format: str, *args: object) -> None:
        # 只记 HTTP 方法和地址，不把患者请求正文写进日志。
        logging.info("%s - %s", self.address_string(), format % args)
