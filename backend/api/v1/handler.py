"""第一版 HTTP 路由和请求校验。"""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from backend.agents.base import AgentConfigurationError
from backend.gateway.orchestration.gateway import Gateway, UnknownRoleError
from backend.shared.tasking.idempotency_store import IdempotencyConflict


gateway = Gateway()


class AgentRequestHandler(BaseHTTPRequestHandler):
    server_version = "AgentService/0.1"

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "找不到这个地址。"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path.strip("/").split("/")
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
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # 只记 HTTP 方法和地址，不把患者请求正文写进日志。
        logging.info("%s - %s", self.address_string(), format % args)
