"""记录运行中的 SSE 请求及客户端取消状态。"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any


@dataclass
class ActiveRun:
    thread_id: str
    future: Future[Any] | None
    events: queue.Queue


class ActiveRunRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, ActiveRun] = {}
        self._interrupted_threads: set[str] = set()

    def reserve(self, run_id: str, thread_id: str, events: queue.Queue) -> bool:
        with self._lock:
            if run_id in self._runs or any(run.thread_id == thread_id for run in self._runs.values()):
                return False
            self._runs[run_id] = ActiveRun(thread_id, None, events)
            return True

    def attach(self, run_id: str, future: Future[Any]) -> None:
        with self._lock:
            active = self._runs[run_id]
            active.future = future

    def finish(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)

    def cancel(self, run_id: str, thread_id: str) -> bool:
        with self._lock:
            active = self._runs.get(run_id)
            if active is None or active.thread_id != thread_id or active.future is None:
                return False
            if not active.future.cancel():
                return False
            self._interrupted_threads.add(thread_id)
            active.events.put(("cancelled", None))
            return True

    def take_interruption(self, thread_id: str) -> bool:
        with self._lock:
            if thread_id not in self._interrupted_threads:
                return False
            self._interrupted_threads.remove(thread_id)
            return True


active_runs = ActiveRunRegistry()
