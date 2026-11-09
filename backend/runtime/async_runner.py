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
        return self.submit(coroutine).result()

    def submit(self, coroutine: Coroutine[Any, Any, Any]) -> Future[Any]:
        """把异步任务交给常驻循环，调用方可以流式读取或主动取消。"""
        if threading.current_thread() is self._thread:
            raise RuntimeError("不能从 Agent 异步事件循环线程重复提交到自身。")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()


_runner: AsyncRunner | None = None
_runner_lock = threading.Lock()


def submit_async(coroutine: Coroutine[Any, Any, Any]) -> Future[Any]:
    """提交异步任务并保留 Future，供 SSE 连接取消正在运行的生成。"""
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = AsyncRunner()
    return _runner.submit(coroutine)


def run_async(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """按需创建共享异步线程，供现有同步 API 调用。"""
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = AsyncRunner()
    return _runner.run(coroutine)
