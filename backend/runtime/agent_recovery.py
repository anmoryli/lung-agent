"""DeepAgents 的短重试和失败转人工处理。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain.agents.middleware import ModelRetryMiddleware, ToolRetryMiddleware
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command


class ManualHandoffRequired(RuntimeError):
    """自动恢复用尽后交给人工队列的内部错误。"""

    def __init__(self, stage: str, tool_name: str = "", *, error_type: str = "Exception") -> None:
        self.stage = stage
        self.tool_name = tool_name
        self.error_type = error_type
        super().__init__(f"{stage} recovery attempts exhausted")


class ManualHandoffMiddleware(AgentMiddleware):
    """包住官方重试层；重试仍失败就停止本轮并交人工队列。"""

    def __init__(self) -> None:
        super().__init__()

    def wrap_model_call(self, request: ModelRequest[Any], handler: Callable[..., Any]) -> Any:
        try:
            return handler(request)
        except GraphBubbleUp:
            raise
        except Exception as error:
            raise ManualHandoffRequired("model", error_type=type(error).__name__) from error

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as error:
            raise ManualHandoffRequired("model", error_type=type(error).__name__) from error

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[..., Any]) -> Any:
        try:
            return handler(request)
        except GraphBubbleUp:
            raise
        except Exception as error:
            raise ManualHandoffRequired(
                "tool", request.tool_call["name"], error_type=type(error).__name__
            ) from error

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[..., Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as error:
            raise ManualHandoffRequired(
                "tool", request.tool_call["name"], error_type=type(error).__name__
            ) from error


def create_recovery_middleware(read_only_tools: set[str]) -> list[AgentMiddleware[Any, Any, Any]]:
    """只重试模型和确认安全的只读工具，写操作不进入重试层。"""
    return [
        ManualHandoffMiddleware(),
        ModelRetryMiddleware(
            max_retries=2,
            on_failure="error",
            initial_delay=0.5,
            max_delay=4.0,
        ),
        ToolRetryMiddleware(
            tools=sorted(read_only_tools),
            max_retries=2,
            on_failure="error",
            initial_delay=0.5,
            max_delay=4.0,
        ),
    ]
