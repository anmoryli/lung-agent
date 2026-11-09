"""给同步 HTTP handler 复用一个常驻事件循环。"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any, Coroutine


class AsyncRunner:
    """运行异步 MCP/DeepAgents 调用，避免每个请求关闭一次网络事件循环。"""

    def __init__(self) -> None:
        self._ready = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="agent-async-loop", daemon=True)
        self._thread.start()
        self._ready.wait()

    def run(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        if threading.current_thread() is self._thread:
            raise RuntimeError("不能在 Agent 异步事件循环线程里同步等待自身。")
        future: Future[Any] = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()


_runner: AsyncRunner | None = None
_runner_lock = threading.Lock()


def run_async(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """按需创建共享异步线程，供现有同步 API 调用。"""
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = AsyncRunner()
    return _runner.run(coroutine)
